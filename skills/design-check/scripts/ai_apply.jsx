// ai_apply.jsx
// design-check スキル: Illustrator ドキュメントへの書き戻し（移動 / ガイド描画）。
//
// 単体では実行できない。ai_bridge.py が
//   var DC_PARAMS = { outDir, file, action, ... };
// を注入し、ai_common.jsx と連結した上で
//   do javascript (POSIX file "<連結済み一時スクリプト>")
// として実行する。
//
// 安全策: このスクリプトは保存を一切行わない（ユーザーが Illustrator 上で確認して
// 自分で保存する）。既存レイヤー・既存オブジェクトの削除も一切行わない。
//
// DC_PARAMS.action == "move":
//   itemName  対象アイテム名（ai_dump.jsx が書き出した dump.json の items[].name と同一の解決規則）
//   dxMm      水平移動量(mm)。+は右
//   dyMm      垂直移動量(mm)。+は下（画面座標に合わせる。Illustrator内部はY上向き正のため符号反転して適用）
//
// DC_PARAMS.action == "guides":
//   centers   [{ label, xPt, yPt }, ...] Illustrator ドキュメント座標系(pt)での光学重心位置
//             「design-check」という名前の新規レイヤー（ロック）に、アートボード中心を通る
//             垂直/水平ガイドと、各 center 位置の短い十字パスを描く。

(function () {
    var outDir = DC_PARAMS.outDir;
    var filePath = DC_PARAMS.file;
    var action = DC_PARAMS.action;

    try {
        var doc = dcFindOrOpenDocument(filePath);

        if (action === "move") {
            var itemName = DC_PARAMS.itemName;
            var dxMm = DC_PARAMS.dxMm || 0;
            var dyMm = DC_PARAMS.dyMm || 0;

            var target = dcFindItemByName(doc, itemName);
            if (!target) {
                throw new Error("アイテムが見つかりません: " + itemName);
            }
            var dxPt = dxMm * MM_TO_PT;
            var dyPt = dyMm * MM_TO_PT;
            var pos = target.position; // [x, y] pt, y上向き正
            target.position = [pos[0] + dxPt, pos[1] - dyPt];

            dcWriteResult(outDir, {
                ok: true, action: "move", itemName: itemName,
                movedDxMm: dxMm, movedDyMm: dyMm,
                newPositionPt: [pos[0] + dxPt, pos[1] - dyPt]
            });
            return;
        }

        if (action === "guides") {
            var centers = DC_PARAMS.centers || [];
            var LAYER_NAME = "design-check";

            var abIndex = doc.artboards.getActiveArtboardIndex();
            var rect = doc.artboards[abIndex].artboardRect; // [L, T, R, B] pt
            var cx = (rect[0] + rect[2]) / 2;
            var cy = (rect[1] + rect[3]) / 2;

            var layer = null;
            for (var i = 0; i < doc.layers.length; i++) {
                if (doc.layers[i].name === LAYER_NAME) { layer = doc.layers[i]; break; }
            }
            if (!layer) {
                layer = doc.layers.add();
                layer.name = LAYER_NAME;
            }
            layer.locked = false;
            layer.visible = true;

            // アートボード中心を通る垂直/水平ガイド（アートボード全域）
            var vLine = layer.pathItems.add();
            vLine.setEntirePath([[cx, rect[1]], [cx, rect[3]]]);
            vLine.filled = false;
            vLine.stroked = false;
            vLine.guides = true;

            var hLine = layer.pathItems.add();
            hLine.setEntirePath([[rect[0], cy], [rect[2], cy]]);
            hLine.filled = false;
            hLine.stroked = false;
            hLine.guides = true;

            var markColor = new RGBColor();
            markColor.red = 230; markColor.green = 0; markColor.blue = 0;
            var halfPt = (3 * MM_TO_PT) / 2; // 3mm幅の十字（片側1.5mm）

            for (var k = 0; k < centers.length; k++) {
                var pt = centers[k];
                var h1 = layer.pathItems.add();
                h1.setEntirePath([[pt.xPt - halfPt, pt.yPt], [pt.xPt + halfPt, pt.yPt]]);
                h1.filled = false; h1.stroked = true; h1.strokeColor = markColor; h1.strokeWidth = 0.5;
                h1.name = "center_" + pt.label + "_h";

                var v1 = layer.pathItems.add();
                v1.setEntirePath([[pt.xPt, pt.yPt - halfPt], [pt.xPt, pt.yPt + halfPt]]);
                v1.filled = false; v1.stroked = true; v1.strokeColor = markColor; v1.strokeWidth = 0.5;
                v1.name = "center_" + pt.label + "_v";
            }

            layer.locked = true;

            dcWriteResult(outDir, { ok: true, action: "guides", layer: LAYER_NAME, centersDrawn: centers.length });
            return;
        }

        throw new Error("不明な action: " + action);
    } catch (e) {
        dcFail(outDir, e);
    }
})();
