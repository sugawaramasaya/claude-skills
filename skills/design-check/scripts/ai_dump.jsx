// ai_dump.jsx
// design-check スキル: Illustrator ドキュメントから解析素材（PNG + dump.json）を書き出す。
//
// 単体では実行できない。ai_bridge.py が
//   var DC_PARAMS = { outDir, file, pngLongEdge, maxScalePct };
// を注入し、ai_common.jsx と連結した上で
//   do javascript (POSIX file "<連結済み一時スクリプト>")
// として実行する。
//
// DC_PARAMS:
//   outDir       出力ディレクトリ（絶対パス）
//   file         対象 .ai ファイルパス（絶対パス）
//   pngLongEdge  PNG 書き出しの目標長辺px（デフォルト 2000）
//   maxScalePct  horizontalScale/verticalScale の上限%（デフォルト 776.19）
//
// 出力:
//   <outDir>/artboard.png   書き出したアートボードPNG
//   <outDir>/dump.json      アートボード情報・全トップレベルpageItem情報
//   <outDir>/dc_result.json 実行結果 { ok: true/false, ... }

(function () {
    var outDir = DC_PARAMS.outDir;
    var filePath = DC_PARAMS.file;
    var pngLongEdge = DC_PARAMS.pngLongEdge || 2000;
    var maxScalePct = DC_PARAMS.maxScalePct || 776.19;

    try {
        var doc = dcFindOrOpenDocument(filePath);

        var abIndex = doc.artboards.getActiveArtboardIndex();
        var ab = doc.artboards[abIndex];
        var rect = ab.artboardRect; // [left, top, right, bottom] pt, y-up
        var wPt = rect[2] - rect[0];
        var hPt = rect[1] - rect[3];
        var longEdgePt = Math.max(wPt, hPt);
        var neededScale = (longEdgePt > 0) ? (pngLongEdge / longEdgePt) * 100 : maxScalePct;
        var scale = Math.min(maxScalePct, neededScale);
        if (scale < 1) { scale = 1; }

        var pngPath = outDir + "/artboard.png";
        var opts = new ExportOptionsPNG24();
        opts.antiAliasing = true;
        opts.transparency = false;
        try { opts.artBoardClipping = true; } catch (eClip) { /* バージョン差異への保険 */ }
        opts.horizontalScale = scale;
        opts.verticalScale = scale;

        var destFile = new File(pngPath);
        doc.exportFile(destFile, ExportType.PNG24, opts);

        var pngDim = dcReadPngDimensions(destFile.fsName);
        var pxPerPt = pngDim.width / wPt;
        var pxPerPtV = pngDim.height / hPt;

        var items = [];
        dcWalkTopLevelItems(doc, function (it, name, layer, layerHidden, layerLocked) {
            try {
                var itemHidden = false, itemLocked = false;
                try { itemHidden = !!it.hidden; } catch (eH) {}
                try { itemLocked = !!it.locked; } catch (eL) {}

                var entry = {
                    name: name,
                    typename: it.typename,
                    layer: layer.name,
                    // レイヤー自体が非表示/ロックの場合も反映した「実効」フラグ
                    // （item.hidden/item.locked はレイヤー側の状態を反映しないため合成する）
                    hidden: itemHidden || layerHidden,
                    locked: itemLocked || layerLocked,
                    guides: false,
                    visibleBoundsPt: it.visibleBounds
                };
                try { entry.guides = !!it.guides; } catch (eG) {}

                if (it.typename === "TextFrame") {
                    var textInfo = { contents: it.contents };
                    try {
                        var ca = it.textRange.characterAttributes;
                        textInfo.fontName = ca.textFont.name;
                        textInfo.fontSize = ca.size;
                        textInfo.tracking = ca.tracking;
                    } catch (eT) { /* 混在スタイル等は取得できる範囲のみ */ }
                    entry.text = textInfo;
                } else {
                    entry.fill = dcRepresentativeFill(it);
                }
                items.push(entry);
            } catch (eItem) {
                items.push({ name: name, typename: "unknown", layer: layer.name, error: String(eItem) });
            }
            return false;
        });

        var dump = {
            sourceFile: filePath,
            docName: doc.name,
            artboard: { name: ab.name, rectPt: rect },
            png: {
                path: destFile.fsName,
                widthPx: pngDim.width,
                heightPx: pngDim.height,
                pxPerPt: pxPerPt,
                pxPerPtVertical: pxPerPtV,
                scalePct: scale
            },
            items: items
        };

        dcWriteTextFile(outDir + "/dump.json", dcToJSON(dump));
        dcWriteResult(outDir, { ok: true, dumpPath: outDir + "/dump.json", pngPath: destFile.fsName, itemCount: items.length });
    } catch (e) {
        dcFail(outDir, e);
    }
})();
