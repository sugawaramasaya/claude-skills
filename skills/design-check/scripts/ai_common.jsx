// ai_common.jsx
// design-check スキル: Illustrator ExtendScript 共通ヘルパー。
// ai_dump.jsx / ai_apply.jsx から ai_bridge.py が連結して実行する（単体では動かない）。
// ai_bridge.py が `var DC_PARAMS = {...};` をこのファイルの直前に注入する前提。

// ---------------------------------------------------------------------------
// 単位
// ---------------------------------------------------------------------------
var MM_TO_PT = 2.834645669;

// ---------------------------------------------------------------------------
// JSON シリアライズ（ExtendScript に JSON.stringify が無いための自前実装）
// ---------------------------------------------------------------------------
function dcJsonEscape(s) {
    s = String(s);
    var out = "";
    for (var i = 0; i < s.length; i++) {
        var ch = s.charAt(i);
        var code = s.charCodeAt(i);
        if (ch === '"') { out += '\\"'; }
        else if (ch === '\\') { out += '\\\\'; }
        else if (ch === '\n') { out += '\\n'; }
        else if (ch === '\r') { out += '\\r'; }
        else if (ch === '\t') { out += '\\t'; }
        else if (code < 0x20) {
            var hex = code.toString(16);
            while (hex.length < 4) { hex = "0" + hex; }
            out += "\\u" + hex;
        } else {
            out += ch;
        }
    }
    return out;
}

function dcToJSON(value) {
    if (value === null || value === undefined) { return "null"; }
    var t = typeof value;
    if (t === "number") {
        return isFinite(value) ? String(value) : "null";
    }
    if (t === "boolean") { return value ? "true" : "false"; }
    if (t === "string") { return '"' + dcJsonEscape(value) + '"'; }
    if (value instanceof Array) {
        var parts = [];
        for (var i = 0; i < value.length; i++) { parts.push(dcToJSON(value[i])); }
        return "[" + parts.join(",") + "]";
    }
    if (t === "object") {
        var kparts = [];
        for (var k in value) {
            if (!value.hasOwnProperty(k)) { continue; }
            kparts.push('"' + dcJsonEscape(k) + '":' + dcToJSON(value[k]));
        }
        return "{" + kparts.join(",") + "}";
    }
    return "null";
}

// ---------------------------------------------------------------------------
// ファイル I/O
// ---------------------------------------------------------------------------
function dcWriteTextFile(path, content) {
    var f = new File(path);
    f.encoding = "UTF-8";
    f.open("w");
    f.write(content);
    f.close();
}

function dcWriteResult(outDir, obj) {
    dcWriteTextFile(outDir + "/dc_result.json", dcToJSON(obj));
}

function dcFail(outDir, err) {
    dcWriteResult(outDir, { ok: false, error: String(err && err.message ? err.message : err) });
}

// PNG の IHDR チャンクから width/height を読む（バイナリ読み込み・自前パース）
function dcReadPngDimensions(pngPath) {
    var f = new File(pngPath);
    f.encoding = "BINARY";
    if (!f.open("r")) { throw new Error("PNGを開けません: " + pngPath); }
    var header = f.read(33);
    f.close();
    function byteAt(str, idx) { return str.charCodeAt(idx) & 0xff; }
    var wOff = 16, hOff = 20;
    var width = (byteAt(header, wOff) * 16777216) + (byteAt(header, wOff + 1) * 65536) +
        (byteAt(header, wOff + 2) * 256) + byteAt(header, wOff + 3);
    var height = (byteAt(header, hOff) * 16777216) + (byteAt(header, hOff + 1) * 65536) +
        (byteAt(header, hOff + 2) * 256) + byteAt(header, hOff + 3);
    return { width: width, height: height };
}

// ---------------------------------------------------------------------------
// ドキュメント解決（既存の他ドキュメントには一切触れない）
// ---------------------------------------------------------------------------
function dcFindOrOpenDocument(filePath) {
    var targetFile = new File(filePath);
    if (!targetFile.exists) {
        throw new Error("ファイルが見つかりません: " + filePath);
    }
    var targetPath = targetFile.fsName;
    for (var i = 0; i < app.documents.length; i++) {
        var d = app.documents[i];
        try {
            if (d.fullName && d.fullName.fsName === targetPath) {
                return d;
            }
        } catch (eIgnored) { /* サンドボックス/未保存ドキュメント等は無視して次へ */ }
    }
    return app.open(targetFile);
}

// ---------------------------------------------------------------------------
// 名前解決（無名アイテムに typename+連番を振る。dump / apply で同一アルゴリズムを使う）
// ---------------------------------------------------------------------------
function dcResolveName(item, counters) {
    var n = "";
    try { n = item.name; } catch (eName) { n = ""; }
    if (n && n.length > 0) { return n; }
    var t = item.typename;
    counters[t] = (counters[t] || 0) + 1;
    return t + "_" + counters[t];
}

// ---------------------------------------------------------------------------
// 色情報の抽出
// ---------------------------------------------------------------------------
function dcColorToObj(color) {
    if (!color) { return { kind: "none" }; }
    var t = color.typename;
    if (t === "NoColor") { return { kind: "none" }; }
    if (t === "CMYKColor") {
        return { kind: "CMYK", c: color.cyan, m: color.magenta, y: color.yellow, k: color.black };
    }
    if (t === "RGBColor") {
        return { kind: "RGB", r: color.red, g: color.green, b: color.blue };
    }
    if (t === "GrayColor") {
        return { kind: "Gray", gray: color.gray };
    }
    if (t === "SpotColor") {
        var inner = dcColorToObj(color.spot.color);
        inner.spotName = color.spot.name;
        inner.tint = color.tint;
        return inner;
    }
    if (t === "PatternColor") { return { kind: "Pattern" }; }
    if (t === "GradientColor") { return { kind: "Gradient" }; }
    return { kind: "unknown", typename: t };
}

// パスの面積（アンカー座標だけを使ったシューレース公式による近似。「面積最大のパス」選定用）
function dcPolygonArea(pathItem) {
    try {
        var pts = pathItem.pathPoints;
        if (pts.length < 3) {
            var vb = pathItem.visibleBounds;
            return Math.abs((vb[2] - vb[0]) * (vb[1] - vb[3]));
        }
        var area = 0;
        for (var i = 0; i < pts.length; i++) {
            var p1 = pts[i].anchor;
            var p2 = pts[(i + 1) % pts.length].anchor;
            area += (p1[0] * p2[1] - p2[0] * p1[1]);
        }
        return Math.abs(area / 2);
    } catch (eArea) {
        try {
            var vb2 = pathItem.visibleBounds;
            return Math.abs((vb2[2] - vb2[0]) * (vb2[1] - vb2[3]));
        } catch (eArea2) { return 0; }
    }
}

function dcCollectLeafPaths(item, list) {
    var t = item.typename;
    if (t === "PathItem") {
        // 塗りが無い（NoColor / filled=false）パスは「見えるインク」ではないため候補から除外する。
        // ロゴ等のグループには当たり判定用の透明な外枠パス（グループ全体と同じ大きさで
        // fillColor=NoColor）が同居していることがあり、これを含めると面積最大選定が
        // 常にその透明パスに吸われてしまう（実機確認 2026-07-03）。
        var isFilled = false;
        try { isFilled = !!item.filled; } catch (eF) { isFilled = false; }
        if (isFilled) { list.push(item); }
    } else if (t === "CompoundPathItem") {
        for (var i = 0; i < item.pathItems.length; i++) {
            var sub = item.pathItems[i];
            var subFilled = false;
            try { subFilled = !!sub.filled; } catch (eSF) { subFilled = false; }
            if (subFilled) { list.push(sub); }
        }
    } else if (t === "GroupItem") {
        for (var j = 0; j < item.pageItems.length; j++) { dcCollectLeafPaths(item.pageItems[j], list); }
    }
    // TextFrame / RasterItem / PlacedItem 等は塗り代表色の対象外
}

// グループ/コンパウンドパスは内部を再帰し、面積最大のパスのフィルを代表色とする
function dcRepresentativeFill(item) {
    var t = item.typename;
    if (t === "PathItem") {
        try { return item.filled ? dcColorToObj(item.fillColor) : { kind: "none" }; }
        catch (e) { return { kind: "unknown" }; }
    }
    if (t === "CompoundPathItem" || t === "GroupItem") {
        var leaves = [];
        dcCollectLeafPaths(item, leaves);
        if (leaves.length === 0) { return { kind: "none" }; }
        var best = null, bestArea = -1;
        for (var i = 0; i < leaves.length; i++) {
            var a = dcPolygonArea(leaves[i]);
            if (a > bestArea) { bestArea = a; best = leaves[i]; }
        }
        try { return best.filled ? dcColorToObj(best.fillColor) : { kind: "none" }; }
        catch (e2) { return { kind: "unknown" }; }
    }
    return { kind: "n/a" };
}

// dump.jsx と apply.jsx (guides の重心指定) の双方が「同じ順序・同じ命名規則」で
// トップレベル pageItem を辿れるようにする共通イテレータ。
//
// callback(item, name, layer, layerHidden, layerLocked) の layerHidden/layerLocked は
// そのアイテムを含む「レイヤー階層（祖先レイヤー含む）」自体の可視/ロック状態を
// OR で畳み込んだもの。Illustrator の DOM では item.hidden / item.locked は
// レイヤー自身の visible=false / locked=true を反映しない（別軸のフラグ）ため、
// 「実際に見えている/操作できるか」を判定するには両方を合成する必要がある。
function dcWalkTopLevelItems(doc, callback) {
    var counters = {};
    function visit(layer, ancestorHidden, ancestorLocked) {
        var layerHidden = ancestorHidden || !layer.visible;
        var layerLocked = ancestorLocked || layer.locked;

        var lvItems;
        try { lvItems = layer.pageItems; } catch (eItems) { lvItems = null; }
        if (lvItems) {
            for (var i = 0; i < lvItems.length; i++) {
                var it = lvItems[i];
                var name = dcResolveName(it, counters);
                var stop = callback(it, name, layer, layerHidden, layerLocked);
                if (stop === true) { return true; }
            }
        }
        var sub;
        try { sub = layer.layers; } catch (eSub) { sub = null; }
        if (sub) {
            for (var s = 0; s < sub.length; s++) {
                if (visit(sub[s], layerHidden, layerLocked) === true) { return true; }
            }
        }
        return false;
    }
    for (var li = 0; li < doc.layers.length; li++) {
        if (visit(doc.layers[li], false, false) === true) { return; }
    }
}

function dcFindItemByName(doc, targetName) {
    var found = null;
    dcWalkTopLevelItems(doc, function (item, name /*, layer, layerHidden, layerLocked */) {
        if (name === targetName) { found = item; return true; }
        return false;
    });
    return found;
}
