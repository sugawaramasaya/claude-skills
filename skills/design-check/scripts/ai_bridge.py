# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""design-check スキル: Illustrator 連携ブリッジ（osascript 経由）。

Illustrator 上のドキュメントを直接操作して解析素材を書き出したり（dump）、
確認済みの修正案を実際に反映したり（apply）、重心ガイドを描いたり（guides）する。

すべて `uv run ai_bridge.py <サブコマンド> ...` の形で実行する。標準出力は JSON のみ。

サブコマンド:
    dump    ドキュメントからPNG書き出し + dump.json 生成
    apply   指定アイテムを mm 単位で移動する（保存はしない）
    guides  「design-check」レイヤーに中心ガイド・重心マークを描く（保存はしない）

安全策:
    - 対象ファイルが既に Illustrator で開かれていればそれを使い、未オープンなら開く
      （ユーザーが開いている他のドキュメントには一切触れない）
    - apply / guides は保存しない。ユーザーが Illustrator 上で確認して自分で保存する
    - 既存レイヤー・既存オブジェクトの削除は一切行わない
    - タイムアウト（60秒）。オートメーション権限が無い場合は迂回せず exit(3) で報告する

終了コード:
    0  成功
    2  引数/入力エラー（ファイルが存在しない等）
    3  オートメーション権限エラー（システム設定での許可が必要）
    4  タイムアウト（Illustrator からの応答が60秒以内に得られなかった）
    5  Illustrator 側（JSX実行）のエラー
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_JSX = SCRIPT_DIR / "ai_common.jsx"
DUMP_JSX = SCRIPT_DIR / "ai_dump.jsx"
APPLY_JSX = SCRIPT_DIR / "ai_apply.jsx"

BUNDLE_ID = "com.adobe.illustrator"
DEFAULT_TIMEOUT_SEC = 60
ILLUSTRATOR_APP_CANDIDATES = [
    "/Applications/Adobe Illustrator 2026/Adobe Illustrator.app",
    "/Applications/Adobe Illustrator 2025/Adobe Illustrator.app",
]

EXIT_USAGE = 2
EXIT_PERMISSION = 3
EXIT_TIMEOUT = 4
EXIT_JS_ERROR = 5

PERMISSION_ERROR_MESSAGE = (
    "Illustrator をコントロールする権限がありません。"
    "システム設定 > プライバシーとセキュリティ > オートメーション で、"
    "実行中のターミナル/Claude Code から Adobe Illustrator へのアクセスを許可してください"
    "（自動での迂回は行いません）。"
)


def _osascript(script: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)


def _illustrator_running() -> bool:
    try:
        proc = _osascript(
            'tell application "System Events" to (name of processes) contains "Adobe Illustrator"',
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def ensure_illustrator_running() -> None:
    """Illustrator が起動していなければ起動する（2026 を優先、無ければ 2025）。"""
    if _illustrator_running():
        return
    launched = False
    for path in ILLUSTRATOR_APP_CANDIDATES:
        if os.path.exists(path):
            subprocess.run(["open", "-a", path])
            launched = True
            break
    if not launched:
        raise RuntimeError("Adobe Illustrator が見つかりません（2025/2026 とも未インストール）")
    for _ in range(30):
        if _illustrator_running():
            return
        time.sleep(1)
    raise RuntimeError("Illustrator の起動待ちがタイムアウトしました")


def _is_permission_error(stderr: str) -> bool:
    lowered = stderr.lower()
    return (
        "-1743" in stderr
        or "1743" in stderr
        or "not authorized to send apple events" in lowered
        or "not allowed" in lowered
        or "許可されていません" in stderr
    )


def build_script(main_jsx_path: Path, params: dict) -> str:
    params_literal = json.dumps(params, ensure_ascii=False)
    common_src = COMMON_JSX.read_text(encoding="utf-8")
    main_src = main_jsx_path.read_text(encoding="utf-8")
    return f"var DC_PARAMS = {params_literal};\n\n{common_src}\n\n{main_src}\n"


def run_jsx(main_jsx_path: Path, params: dict, out_dir: Path, timeout: int = DEFAULT_TIMEOUT_SEC) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "dc_result.json"
    if result_path.exists():
        result_path.unlink()

    try:
        ensure_illustrator_running()
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(EXIT_TIMEOUT)

    run_id = uuid.uuid4().hex[:8]
    script_text = build_script(main_jsx_path, params)
    script_path = out_dir / f"_dc_run_{run_id}.jsx"
    script_path.write_text(script_text, encoding="utf-8")

    applescript = f'tell application id "{BUNDLE_ID}" to do javascript (POSIX file "{script_path}")'

    start = time.time()
    proc = None
    try:
        proc = _osascript(applescript, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc = None  # AppleEvent がタイムアウトしても JSX 自体は完走することがあるので、後続でポーリングする

    if proc is not None and proc.returncode != 0:
        stderr = proc.stderr or ""
        if _is_permission_error(stderr):
            print(f"error: {PERMISSION_ERROR_MESSAGE}\n(osascript stderr: {stderr.strip()})", file=sys.stderr)
            sys.exit(EXIT_PERMISSION)
        print(f"error: osascript 実行エラー: {stderr.strip()}", file=sys.stderr)
        sys.exit(EXIT_JS_ERROR)

    remaining = timeout - (time.time() - start)
    deadline = time.time() + max(0.0, remaining)
    data = None
    while time.time() < deadline:
        if result_path.exists():
            try:
                text = result_path.read_text(encoding="utf-8").strip()
                if text:
                    data = json.loads(text)
                    break
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(0.5)

    if data is None:
        print(
            "error: Illustrator からの応答がタイムアウトしました（60秒）。"
            "処理が完了していない可能性があります。Illustrator側の状態を確認してください。",
            file=sys.stderr,
        )
        sys.exit(EXIT_TIMEOUT)

    if not data.get("ok"):
        print(f"error: Illustrator 側でエラーが発生しました: {data.get('error')}", file=sys.stderr)
        sys.exit(EXIT_JS_ERROR)

    return data


def _require_file(path: str) -> Path:
    p = Path(path)
    if not p.exists():
        print(f"error: ファイルが見つかりません: {path}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    return p.resolve()


def cmd_dump(args) -> None:
    file_path = _require_file(args.file)
    out_dir = Path(args.out_dir).resolve()
    params = {
        "outDir": str(out_dir),
        "file": str(file_path),
        "pngLongEdge": args.png_long_edge,
        "maxScalePct": args.max_scale_pct,
    }
    data = run_jsx(DUMP_JSX, params, out_dir, timeout=args.timeout)
    print(json.dumps(data, ensure_ascii=False))


def cmd_apply(args) -> None:
    file_path = _require_file(args.file)
    out_dir = Path(args.out_dir).resolve()
    params = {
        "outDir": str(out_dir),
        "file": str(file_path),
        "action": "move",
        "itemName": args.item_name,
        "dxMm": args.dx_mm,
        "dyMm": args.dy_mm,
    }
    data = run_jsx(APPLY_JSX, params, out_dir, timeout=args.timeout)
    print(json.dumps(data, ensure_ascii=False))


def cmd_guides(args) -> None:
    file_path = _require_file(args.file)
    out_dir = Path(args.out_dir).resolve()
    centers_path = Path(args.centers)
    if not centers_path.exists():
        print(f"error: centersファイルが見つかりません: {args.centers}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    centers = json.loads(centers_path.read_text(encoding="utf-8"))
    params = {
        "outDir": str(out_dir),
        "file": str(file_path),
        "action": "guides",
        "centers": centers,
    }
    data = run_jsx(APPLY_JSX, params, out_dir, timeout=args.timeout)
    print(json.dumps(data, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="design-check: Illustrator 連携ブリッジ (osascript 経由)")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dump", help="ドキュメントから PNG + dump.json を書き出す")
    d.add_argument("--file", required=True, help="対象 .ai ファイルパス")
    d.add_argument("--out-dir", required=True)
    d.add_argument("--png-long-edge", type=int, default=2000, help="PNG書き出しの目標長辺px（デフォルト2000）")
    d.add_argument("--max-scale-pct", type=float, default=776.19, help="horizontalScale/verticalScaleの上限%")
    d.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC)
    d.set_defaults(func=cmd_dump)

    a = sub.add_parser("apply", help="指定アイテムをmm単位で移動する（保存はしない）")
    a.add_argument("--file", required=True)
    a.add_argument("--item-name", required=True, help="dump.json の items[].name と同一の名前")
    a.add_argument("--dx-mm", type=float, default=0.0, help="水平移動量(mm)。+は右")
    a.add_argument("--dy-mm", type=float, default=0.0, help="垂直移動量(mm)。+は下")
    a.add_argument("--out-dir", required=True)
    a.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC)
    a.set_defaults(func=cmd_apply)

    g = sub.add_parser("guides", help="「design-check」レイヤーに中心ガイド・重心マークを描く（保存はしない）")
    g.add_argument("--file", required=True)
    g.add_argument("--centers", required=True, help="[{label,xPt,yPt}] のJSONファイルパス（ai_to_rois.py centers参照）")
    g.add_argument("--out-dir", required=True)
    g.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC)
    g.set_defaults(func=cmd_guides)

    return p


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
