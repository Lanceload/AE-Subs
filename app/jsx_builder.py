"""Генерация ExtendScript (.jsx) для After Effects.

Скрипты самодостаточные: данные вшиты в них. Результат AE записывает в JSON-файл,
который читает программа (нужна настройка AE «Разрешить скриптам записывать файлы»;
если она выключена — результат показывается обычным alert).
ExtendScript — это ES3: никаких let/const, стрелочных функций и встроенного JSON.
"""
from __future__ import annotations

import json
import re

from .models import CHAR_KEYS, AESettings, Style, Subtitle, wrap_text

# ---------------------------------------------------------------- общие функции

JSX_COMMON = r"""
function toJSON(v) {
    if (v === null || v === undefined) return "null";
    var t = typeof v;
    if (t == "number") return isFinite(v) ? String(v) : "null";
    if (t == "boolean") return v ? "true" : "false";
    if (t == "string") {
        var s = "", c;
        for (var i = 0; i < v.length; i++) {
            c = v.charCodeAt(i);
            if (c == 34) s += '\\"';
            else if (c == 92) s += "\\\\";
            else if (c < 32 || c > 126) { var h = c.toString(16); while (h.length < 4) h = "0" + h; s += "\\u" + h; }
            else s += v.charAt(i);
        }
        return '"' + s + '"';
    }
    if (v instanceof Array) {
        var a = [];
        for (var j = 0; j < v.length; j++) a.push(toJSON(v[j]));
        return "[" + a.join(",") + "]";
    }
    var parts = [];
    for (var k in v) if (v.hasOwnProperty(k)) parts.push(toJSON(String(k)) + ":" + toJSON(v[k]));
    return "{" + parts.join(",") + "}";
}

function writeResult(path, obj, fallbackMessage) {
    try {
        var tmp = new File(path + ".tmp");
        tmp.encoding = "UTF-8";
        if (!tmp.open("w")) throw new Error("open failed");
        tmp.write(toJSON(obj));
        tmp.close();
        var dst = new File(path);
        if (dst.exists) dst.remove();
        if (!tmp.rename(dst.name)) throw new Error("rename failed");
        return true;
    } catch (e) {
        alert(fallbackMessage + "\n\n(Чтобы программа видела ответ AE, включите: Правка → Настройки → " +
              "Скрипты и выражения → «Разрешить скриптам записывать файлы и получать доступ к сети».)", "AE Subs");
        return false;
    }
}

// Основная композиция — открытая в AE (с видео и звуком)
function mainComp() {
    var main = app.project.activeItem;
    if (!(main && main instanceof CompItem)) {
        // активной может не быть, если фокус на панели проекта — берём открытую в окне композиции
        try { if (app.activeViewer && app.activeViewer.type == ViewerType.VIEWER_COMPOSITION) {
            app.activeViewer.setActive(); main = app.project.activeItem; } } catch (e) {}
    }
    if (!(main && main instanceof CompItem)) throw new Error("NO_COMP");
    return main;
}

function findHolder(main, name) {
    for (var i = 1; i <= main.numLayers; i++) {
        var l = main.layer(i);
        if (l.source && l.source instanceof CompItem && l.source.name == name) return l;
    }
    return null;
}

// В AE изменение inPoint у слоя без исходника сдвигает слой целиком, а не обрезает его,
// поэтому порядок важен: сначала начало, потом конец.
function setTiming(layer, start, end) {
    layer.inPoint = start;
    layer.outPoint = end;
    if (Math.abs(layer.inPoint - start) > 0.001) layer.inPoint = start;
}
"""

# ---------------------------------------------------------------- вставка

JSX_APPLY = r"""
(function () {
var DATA = /*DATA*/;
var A = DATA.ae, T = DATA.track, STYLES = DATA.styles, ITEMS = DATA.items;
var R = {ok: false, added: 0, skipped: 0, warnings: [], comp: "", font: "", font_ok: true, style_ok: true};
var S = null;  // стиль текущего элемента

function hex(c) {
    c = c.replace("#", "");
    return [parseInt(c.substr(0, 2), 16) / 255, parseInt(c.substr(2, 2), 16) / 255, parseInt(c.substr(4, 2), 16) / 255];
}
function pad(n) { var s = String(n); while (s.length < 3) s = "0" + s; return s; }
function lower(s) { return String(s || "").toLowerCase(); }
function prop(layer, path) {
    var p = layer;
    for (var i = 0; i < path.length; i++) { p = p.property(path[i]); if (!p) return null; }
    return p;
}

// ---- шрифты: кандидаты PostScript-имени (от программы и поиск средствами AE), результат кешируем
var FONT_CACHE = {};
function fontCandidates(st) {
    var list = [];
    function add(n) { if (n) { for (var i = 0; i < list.length; i++) if (list[i] == n) return; list.push(n); } }
    add(st.font_ps);
    try {
        var found = app.fonts.getFontsByFamilyNameAndStyleName(st.font_family, st.font_style);
        for (var i = 0; i < found.length; i++) add(found[i].postScriptName);
    } catch (e) {}
    try {
        var fam = lower(st.font_family), sty = lower(st.font_style);
        if (sty == "normal") sty = "regular";
        var full = sty == "regular" ? fam : fam + " " + sty;
        var groups = app.fonts.allFonts;
        for (var g = 0; g < groups.length; g++) {
            for (var j = 0; j < groups[g].length; j++) {
                var f = groups[g][j], fs = lower(f.styleName);
                if (fs == "normal") fs = "regular";
                if ((lower(f.familyName) == fam && fs == sty) || lower(f.familyName + " " + f.styleName) == full)
                    add(f.postScriptName);
            }
        }
    } catch (e) {}
    var guess = st.font_family.replace(/ /g, "");
    if (lower(st.font_style) != "regular" && lower(st.font_style) != "normal") guess += "-" + st.font_style.replace(/ /g, "");
    add(guess);
    return list;
}

function setFont(p) {
    var key = S.font_family + "|" + S.font_style;
    var cached = FONT_CACHE[key];
    var tryList = cached === undefined ? fontCandidates(S) : (cached ? [cached] : []);
    for (var i = 0; i < tryList.length; i++) {
        try {
            var d = p.value;
            d.font = tryList[i];
            p.setValue(d);
            if (lower(p.value.font) == lower(tryList[i])) { FONT_CACHE[key] = tryList[i]; R.font = tryList[i]; return true; }
        } catch (e) {}
    }
    FONT_CACHE[key] = null;
    return false;
}

// ---- стиль отдельных слов: форматирование части текста (TextDocument.characterRange, AE 24+)
var RANGE_FONT = {};
function rangeFont(q) {
    var key = q.font_family + "|" + q.font_style;
    if (RANGE_FONT[key] !== undefined) return RANGE_FONT[key];
    var name = null;
    try {
        var f = app.fonts.getFontsByFamilyNameAndStyleName(q.font_family, q.font_style);
        if (f && f.length) name = f[0].postScriptName;
    } catch (e) {}
    if (!name) name = q.font_ps || null;
    RANGE_FONT[key] = name;
    return name;
}

// role: main — цвета и обводка слова; dup (слои обводки/свечения) — только то, что меняет форму букв
function applyRanges(p, ranges, role) {
    var doc = p.value;
    if (typeof doc.characterRange != "function") { R.no_word_styles = true; return; }
    for (var i = 0; i < ranges.length; i++) {
        var r = ranges[i], q = r.p;
        try {
            var cr = doc.characterRange(r.s, r.e);
            if (q.font_family !== undefined) { var fn = rangeFont(q); if (fn) cr.font = fn; }
            if (q.font_size !== undefined) cr.fontSize = q.font_size;
            if (q.faux_bold !== undefined) cr.fauxBold = q.faux_bold;
            if (q.faux_italic !== undefined) cr.fauxItalic = q.faux_italic;
            if (q.tracking !== undefined) cr.tracking = q.tracking;
            if (q.h_scale !== undefined) cr.horizontalScale = q.h_scale / 100;
            if (q.v_scale !== undefined) cr.verticalScale = q.v_scale / 100;
            if (q.all_caps !== undefined && typeof FontCapsOption != "undefined")
                cr.fontCapsOption = q.all_caps ? FontCapsOption.FONT_ALL_CAPS : FontCapsOption.FONT_NORMAL_CAPS;
            if (role == "main") {
                if (q.fill_color !== undefined) { cr.applyFill = true; cr.fillColor = hex(q.fill_color); }
                if (q.stroke_enabled !== undefined || q.stroke_width !== undefined || q.stroke_color !== undefined) {
                    var on = q.stroke_enabled !== undefined ? q.stroke_enabled : S.stroke_enabled;
                    if (on) {
                        cr.applyStroke = true;
                        cr.strokeColor = hex(q.stroke_color !== undefined ? q.stroke_color : S.stroke_color);
                        cr.strokeWidth = q.stroke_width !== undefined ? q.stroke_width : S.stroke_width;
                    } else {
                        cr.applyStroke = false;
                    }
                }
            }
        } catch (e) {
            R.range_errors = (R.range_errors || 0) + 1;
            R.range_error = e.toString();
        }
    }
    p.setValue(doc);
}

// ---- стиль текстового слоя. fill/stroke передаются явно: у слоёв-обводок они свои
function styleText(layer, text, fillColor, strokeColor, strokeWidth, ranges, role) {
    var p = layer.property("ADBE Text Properties").property("ADBE Text Document");
    // 1) всё, кроме шрифта: если AE не примет шрифт, остальной стиль всё равно применится
    var doc = p.value;
    doc.text = text;
    doc.fontSize = S.font_size;
    doc.applyFill = true;
    doc.fillColor = hex(fillColor);
    if (strokeWidth > 0) {
        doc.applyStroke = true;
        doc.strokeColor = hex(strokeColor);
        doc.strokeWidth = strokeWidth;
        doc.strokeOverFill = S.stroke_over_fill;
    } else {
        doc.applyStroke = false;
    }
    try { doc.tracking = S.tracking; } catch (e) {}
    try {
        if (S.auto_leading) doc.autoLeading = true;
        else { doc.autoLeading = false; doc.leading = S.leading; }
    } catch (e) {}
    doc.justification = S.justification == "left" ? ParagraphJustification.LEFT_JUSTIFY :
                        S.justification == "right" ? ParagraphJustification.RIGHT_JUSTIFY :
                        ParagraphJustification.CENTER_JUSTIFY;
    // allCaps в новых версиях AE только для чтения — капс включается через fontCapsOption
    if (typeof FontCapsOption != "undefined") {
        try { doc.fontCapsOption = S.all_caps ? FontCapsOption.FONT_ALL_CAPS : FontCapsOption.FONT_NORMAL_CAPS; } catch (e) {}
    }
    try { doc.fauxBold = S.faux_bold; } catch (e) {}
    try { doc.fauxItalic = S.faux_italic; } catch (e) {}
    try { doc.baselineShift = 0; } catch (e) {}
    // масштаб текста в AE — множитель (1 = 100%), а не проценты
    try { doc.horizontalScale = S.h_scale / 100; doc.verticalScale = S.v_scale / 100; } catch (e) {}
    p.setValue(doc);

    var check = p.value;
    if (Math.abs(check.fontSize - S.font_size) > 0.5) R.style_ok = false;
    if (S.all_caps && !check.allCaps) {
        check.text = text.toUpperCase();  // старая версия AE без fontCapsOption
        p.setValue(check);
    }
    // 2) шрифт отдельно
    if (!setFont(p)) R.font_ok = false;
    // группировка по словам — чтобы увеличение подсветки шло от центра слова
    try {
        var more = prop(layer, ["ADBE Text Properties", "ADBE Text More Options"]);
        more.property("ADBE Text Anchor Point Option").setValue(2);
        more.property("ADBE Text Anchor Point Align").setValue([0, -50]);
    } catch (e) {}
    if (ranges && ranges.length) applyRanges(p, ranges, role || "main");
}

// Точка привязки по границам текста. Границы берём в момент, когда видны все слова
// (иначе появление по словам/буквам сдвигает текст).
function anchorExpression(perLine, measureTime) {
    var x = S.justification == "left" ? "r.left" :
            S.justification == "right" ? "r.left + r.width" : "r.left + r.width / 2";
    var r = "var r = sourceRectAtTime(" + (Math.round(measureTime * 1000) / 1000) + ", false);\n";
    if (perLine) return r + "[" + x + ", 0];";
    var y = S.vertical_anchor == "top" ? "r.top" :
            S.vertical_anchor == "center" ? "r.top + r.height / 2" : "r.top + r.height";
    return r + "[" + x + ", " + y + "];";
}

// ---- эффекты (свойства ищем по типу — так не зависим от номеров параметров)
function setByType(fx, type, value, nth) {
    var seen = 0;
    for (var i = 1; i <= fx.numProperties; i++) {
        var p = fx.property(i);
        try {
            if (p.propertyValueType == type) {
                if ((nth || 0) == seen) { p.setValue(value); return p; }
                seen++;
            }
        } catch (e) {}
    }
    return null;
}

function addShadow(layer) {
    var fx = layer.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");
    fx.property("ADBE Drop Shadow-0001").setValue(hex(S.shadow_color));
    var op = fx.property("ADBE Drop Shadow-0002");
    // в старых версиях AE непрозрачность тени хранится в диапазоне 0..255
    op.setValue(op.hasMax && op.maxValue > 100 ? S.shadow_opacity * 2.55 : S.shadow_opacity);
    fx.property("ADBE Drop Shadow-0003").setValue(S.shadow_direction);
    fx.property("ADBE Drop Shadow-0004").setValue(S.shadow_distance);
    fx.property("ADBE Drop Shadow-0005").setValue(S.shadow_softness);
}

function addBlur(layer, amount) {
    var fx = layer.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
    var b = fx.property("ADBE Gaussian Blur 2-0001");
    b.setValue(amount);
    try { fx.property("ADBE Gaussian Blur 2-0003").setValue(1); } catch (e) {}  // не обрезать края
    return b;
}

// Градиентная заливка: «Градиентная заливка» по прямоугольнику текста + «Установить подложку» по самому слою
function addGradient(layer) {
    var fx = layer.property("ADBE Effect Parade").addProperty("ADBE Ramp");
    var geo = "var r = sourceRectAtTime(time, false);\nvar c = [r.left + r.width / 2, r.top + r.height / 2];\n" +
              "var a = degreesToRadians(" + S.gradient_angle + ");\n" +
              "var h = (Math.abs(Math.cos(a)) * r.width + Math.abs(Math.sin(a)) * r.height) / 2;\n";
    fx.property("ADBE Ramp-0001").expression = geo + "[c[0] - Math.cos(a) * h, c[1] - Math.sin(a) * h];";
    fx.property("ADBE Ramp-0003").expression = geo + "[c[0] + Math.cos(a) * h, c[1] + Math.sin(a) * h];";
    fx = layer.property("ADBE Effect Parade").property(layer.property("ADBE Effect Parade").numProperties);
    fx.property("ADBE Ramp-0002").setValue(hex(S.fill_color));
    fx.property("ADBE Ramp-0004").setValue(hex(S.fill_color2));
    var sm = layer.property("ADBE Effect Parade").addProperty("ADBE Set Matte3");
    sm.property("ADBE Set Matte3-0001").setValue(layer.index);
    sm.property("ADBE Set Matte3-0002").setValue(4);  // альфа-канал
}

// Копия текста под основным слоем: вторая обводка, обводка при градиенте, свечение.
// Привязана к основному слою: двигается, крутится и исчезает вместе с ним, текст берёт у него.
function addDup(comp, main, after, name, text, fillColor, strokeColor, strokeWidth, ranges) {
    var d = comp.layers.addText(text);
    d.name = name;
    styleText(d, text, fillColor, strokeColor, strokeWidth, ranges, "dup");
    d.moveAfter(after);
    d.parent = main;
    d.transform.anchorPoint.setValue([0, 0]);
    d.transform.position.setValue([0, 0]);
    d.transform.scale.setValue([100, 100]);
    d.transform.rotation.setValue(0);
    d.transform.opacity.expression = "thisLayer.parent.transform.opacity * value / 100";
    // строка вместо текстового документа — так у копии остаётся свой стиль.
    // Если у слов свой стиль, выражение не ставим: строка потеряла бы форматирование частей текста.
    if (!(ranges && ranges.length))
        prop(d, ["ADBE Text Properties", "ADBE Text Document"]).expression = '"" + thisLayer.parent.text.sourceText';
    return d;
}

function addBox(comp, main, after, name) {
    var box = comp.layers.addShape();
    box.name = name;
    box.moveAfter(after);
    box.parent = main;
    box.transform.anchorPoint.setValue([0, 0]);
    box.transform.position.setValue([0, 0]);
    box.transform.scale.setValue([100, 100]);
    box.transform.rotation.setValue(0);
    box.transform.opacity.expression = "thisLayer.parent.transform.opacity";

    var grp = box.property("ADBE Root Vectors Group").addProperty("ADBE Vector Group");
    grp.name = "Plate";
    var rect = grp.property("ADBE Vectors Group").addProperty("ADBE Vector Shape - Rect");
    rect.property("ADBE Vector Rect Size").expression =
        "var r = thisLayer.parent.sourceRectAtTime(time, false);\n" +
        "[r.width + " + (S.box_padding_x * 2) + ", r.height + " + (S.box_padding_y * 2) + "];";
    rect.property("ADBE Vector Rect Position").expression =
        "var r = thisLayer.parent.sourceRectAtTime(time, false);\n[r.left + r.width / 2, r.top + r.height / 2];";
    rect.property("ADBE Vector Rect Roundness").setValue(S.box_roundness);
    // после addProperty старые ссылки в той же группе становятся невалидными
    var vecs = box.property("ADBE Root Vectors Group").property(1).property("ADBE Vectors Group");
    var fill = vecs.addProperty("ADBE Vector Graphic - Fill");
    fill.property("ADBE Vector Fill Color").setValue(hex(S.box_color));
    fill.property("ADBE Vector Fill Opacity").setValue(S.box_opacity);
    return box;
}

// ---- текстовые аниматоры (на основном слое и на копиях, чтобы всё двигалось одинаково)
function addAnimator(layer, name) {
    var animators = prop(layer, ["ADBE Text Properties", "ADBE Text Animators"]);
    var an = animators.addProperty("ADBE Text Animator");
    an.name = name;
    return animators.property(animators.numProperties);
}

function animatorRef(layer) {
    var animators = prop(layer, ["ADBE Text Properties", "ADBE Text Animators"]);
    return animators.property(animators.numProperties);
}

// Появление по буквам / по словам: аниматор непрозрачности + диапазон, который «уезжает»
function addReveal(layer, start, dur, byWords, pop) {
    var an = addAnimator(layer, (byWords ? "Words" : "Letters") + (pop ? " Pop" : " In"));
    an.property("ADBE Text Animator Properties").addProperty("ADBE Text Opacity").setValue(0);
    an = animatorRef(layer);
    // «выскакивание»: невыделенные ещё буквы/слова маленькие и растут по мере появления
    if (pop) an.property("ADBE Text Animator Properties").addProperty("ADBE Text Scale 3D").setValue([0, 0, 100]);
    an = animatorRef(layer);
    an.property("ADBE Text Selectors").addProperty("ADBE Text Selector");
    an = animatorRef(layer);
    var sel = an.property("ADBE Text Selectors").property(1);
    if (byWords) {
        try { sel.property("ADBE Text Range Advanced").property("ADBE Text Range Type2").setValue(3); } catch (e) {}
    }
    var st = sel.property("ADBE Text Percent Start");
    st.setValueAtTime(start, 0);
    st.setValueAtTime(start + dur, 100);
}

// Подсветка слов: выражение по таймингам слов включает аниматор только для нужного слова
function wordExpression(times, cond) {
    var arr = [];
    for (var i = 0; i < times.length; i++) arr.push(Math.round(times[i][0] * 1000) / 1000);
    return "var s = [" + arr.join(",") + "];\nvar k = -1;\n" +
           "for (var j = 0; j < s.length; j++) { if (time >= s[j]) k = j; }\n" +
           "var i = textIndex - 1;\n(" + cond + ") ? 100 : 0;";
}

function addWordAnimator(layer, name, times, cond, kind, value) {
    var an = addAnimator(layer, name);
    an.property("ADBE Text Animator Properties").addProperty(kind).setValue(value);
    an = animatorRef(layer);
    an.property("ADBE Text Selectors").addProperty("ADBE Text Expressible Selector");
    an = animatorRef(layer);
    var sel = an.property("ADBE Text Selectors").property(1);
    try { sel.property("ADBE Text Range Type2").setValue(3); } catch (e) {}  // по словам
    sel.property("ADBE Text Expressible Amount").expression = wordExpression(times, cond);
}

// role: main — основной слой, dup — копия-обводка/свечение, hl — слой подсветки поверх градиента
function textAnimators(layer, role, start, dIn, times) {
    if (S.anim_in == "typewriter") addReveal(layer, start, dIn, false, false);
    if (S.anim_in == "letters_pop") addReveal(layer, start, dIn, false, true);
    if (S.anim_in == "words") addReveal(layer, start, dIn, true, false);
    if (S.anim_in == "words_pop") addReveal(layer, start, dIn, true, true);
    if (S.hl_enabled && times.length) {
        var lit = S.hl_mode == "karaoke" ? "i <= k" : "i == k";
        if (role == "main" && S.fill_type != "gradient")
            addWordAnimator(layer, "Highlight Color", times, lit, "ADBE Text Fill Color", hex(S.hl_color));
        if (role == "hl")  // на слое подсветки видны только звучащие слова
            addWordAnimator(layer, "Highlight Mask", times, "!(" + lit + ")", "ADBE Text Opacity", 0);
        if (Math.abs(S.hl_scale - 100) > 0.5)
            addWordAnimator(layer, "Highlight Scale", times, "i == k", "ADBE Text Scale 3D", [S.hl_scale, S.hl_scale, 100]);
        if (S.hl_reveal && role != "hl") addWordAnimator(layer, "Highlight Reveal", times, "i > k", "ADBE Text Opacity", 0);
    }
}

function ease(p) {
    try {
        var n = 1;
        var t = p.propertyValueType;
        if (t != PropertyValueType.TwoD_SPATIAL && t != PropertyValueType.ThreeD_SPATIAL && p.value instanceof Array)
            n = p.value.length;
        var arr = [];
        for (var i = 0; i < n; i++) arr.push(new KeyframeEase(0, 66));
        for (var k = 1; k <= p.numKeys; k++) p.setTemporalEaseAtKey(k, arr, arr);
    } catch (e) {}
}

// Появление/исчезновение слоя целиком (копии и плашка следуют за ним через родителя)
function animate(layer, start, end, basePos, dIn, dOut, blurLayers) {
    var tr = layer.transform;
    var op = tr.opacity, sc = tr.scale, pos = tr.position, rot = tr.rotation;
    var d = S.slide_distance, x = basePos[0], y = basePos[1], r0 = S.rotation;
    var A_IN = S.anim_in, A_OUT = S.anim_out;
    var fadeIn = {fade: 1, slide: 1, drop: 1, slide_left: 1, slide_right: 1, rotate: 1, scale: 1, stretch: 1, blur: 1};
    var shift = {slide: [0, d], drop: [0, -d], slide_left: [-d, 0], slide_right: [d, 0], rise: [0, -d]};

    // ---- появление
    if (fadeIn[A_IN]) { op.setValueAtTime(start, 0); op.setValueAtTime(start + dIn, 100); }
    if (A_IN == "pop") {
        op.setValueAtTime(start, 0); op.setValueAtTime(start + dIn * 0.5, 100);
        sc.setValueAtTime(start, [70, 70, 100]);
        sc.setValueAtTime(start + dIn * 0.6, [108, 108, 100]);
        sc.setValueAtTime(start + dIn, [100, 100, 100]);
    }
    if (A_IN == "bounce") {
        op.setValueAtTime(start, 0); op.setValueAtTime(start + dIn * 0.3, 100);
        sc.setValueAtTime(start, [0, 0, 100]);
        sc.setValueAtTime(start + dIn * 0.45, [115, 115, 100]);
        sc.setValueAtTime(start + dIn * 0.7, [95, 95, 100]);
        sc.setValueAtTime(start + dIn, [100, 100, 100]);
    }
    if (A_IN == "scale") { sc.setValueAtTime(start, [0, 0, 100]); sc.setValueAtTime(start + dIn, [100, 100, 100]); }
    if (A_IN == "stretch") { sc.setValueAtTime(start, [0, 100, 100]); sc.setValueAtTime(start + dIn, [100, 100, 100]); }
    if (shift[A_IN]) {
        pos.setValueAtTime(start, [x + shift[A_IN][0], y + shift[A_IN][1]]);
        pos.setValueAtTime(start + dIn, basePos);
    }
    if (A_IN == "rotate") {
        rot.setValueAtTime(start, r0 - 12); rot.setValueAtTime(start + dIn, r0);
        sc.setValueAtTime(start, [80, 80, 100]); sc.setValueAtTime(start + dIn, [100, 100, 100]);
    }

    // ---- исчезновение
    if (A_OUT != "none") { op.setValueAtTime(end - dOut, 100); op.setValueAtTime(end, 0); }
    if (A_OUT == "pop") { sc.setValueAtTime(end - dOut, [100, 100, 100]); sc.setValueAtTime(end, [70, 70, 100]); }
    if (A_OUT == "scale") { sc.setValueAtTime(end - dOut, [100, 100, 100]); sc.setValueAtTime(end, [0, 0, 100]); }
    if (A_OUT == "stretch") { sc.setValueAtTime(end - dOut, [100, 100, 100]); sc.setValueAtTime(end, [0, 100, 100]); }
    if (shift[A_OUT]) {
        pos.setValueAtTime(end - dOut, basePos);
        pos.setValueAtTime(end, [x + shift[A_OUT][0], y + shift[A_OUT][1]]);
    }
    if (A_OUT == "rotate") {
        rot.setValueAtTime(end - dOut, r0); rot.setValueAtTime(end, r0 + 12);
        sc.setValueAtTime(end - dOut, [100, 100, 100]); sc.setValueAtTime(end, [80, 80, 100]);
    }
    if (A_IN == "blur" || A_OUT == "blur") {
        for (var i = 0; i < blurLayers.length; i++) {
            var b = addBlur(blurLayers[i], 0);
            if (A_IN == "blur") { b.setValueAtTime(start, 40); b.setValueAtTime(start + dIn, 0); }
            if (A_OUT == "blur") { b.setValueAtTime(end - dOut, 0); b.setValueAtTime(end, 40); }
            ease(b);
        }
    }
    ease(op); ease(sc); ease(pos); ease(rot);
}

function getComp() {
    var main = mainComp();
    R.main = main.name;
    R.width = main.width; R.height = main.height; R.fps = main.frameRate; R.duration = main.duration;
    if (A.target == "layers") return main;
    var name = T.comp_name + " (" + main.name + ")";
    var holder = findHolder(main, name);
    var sub;
    if (holder) {
        sub = holder.source;
        sub.width = main.width; sub.height = main.height;
        sub.pixelAspect = main.pixelAspect; sub.frameRate = main.frameRate;
        sub.duration = main.duration;
    } else {
        sub = app.project.items.addComp(name, main.width, main.height, main.pixelAspect, main.duration, main.frameRate);
        holder = main.layers.add(sub);
    }
    holder.locked = false;
    holder.startTime = 0;
    setTiming(holder, 0, main.duration);
    holder.moveToBeginning();
    return sub;
}

function removeOld(comp) {
    var p = T.prefix + " ";
    for (var i = comp.numLayers; i >= 1; i--) {
        var l = comp.layer(i);
        if (l.name.indexOf(p) == 0) { l.locked = false; l.remove(); }
    }
}

// Один «блок» текста (весь субтитр или одна строка при плашке по строкам) со всеми слоями
function buildUnit(comp, baseName, text, start, end, times, basePos, perLine, dIn, dOut, ranges) {
    var gradient = S.fill_type == "gradient";
    var w1 = S.stroke_enabled ? S.stroke_width : 0;
    var w2 = S.stroke2_enabled ? S.stroke2_width : 0;

    var main = comp.layers.addText(text);
    var shortText = text.replace(/\r/g, " ");
    if (shortText.length > 30) shortText = shortText.substr(0, 30) + "...";
    main.name = baseName + " | " + shortText;
    // при градиенте обводка уходит на отдельный слой, иначе градиент закрасит и её
    styleText(main, text, S.fill_color, S.stroke_color, gradient ? 0 : w1, ranges, "main");

    var last = main, silhouette = main, dups = [];
    if (gradient && w1 > 0) {
        last = addDup(comp, main, last, baseName + " STROKE", text, S.stroke_color, S.stroke_color, w1, ranges);
        silhouette = last; dups.push(last);
    }
    if (w2 > 0) {
        last = addDup(comp, main, last, baseName + " STROKE2", text, S.stroke2_color, S.stroke2_color, w1 + 2 * w2,
                      ranges);
        silhouette = last; dups.push(last);
    }
    if (S.glow_enabled) {
        var gw = w2 > 0 ? w1 + 2 * w2 : w1;
        last = addDup(comp, main, last, baseName + " GLOW", text, S.glow_color, S.glow_color, gw, ranges);
        addBlur(last, S.glow_radius);
        last.transform.opacity.expression = "thisLayer.parent.transform.opacity * " + (S.glow_opacity / 100);
        dups.push(last);
    }
    if (S.box_enabled) last = addBox(comp, main, last, baseName + " BOX");
    // градиент закрашивает подсветку цветом — поэтому при градиенте подсветка идёт отдельным слоем сверху
    var hlLayer = null;
    if (gradient && S.hl_enabled && times.length) {
        hlLayer = addDup(comp, main, main, baseName + " HL", text, S.hl_color, S.hl_color, 0, ranges);
        hlLayer.moveBefore(main);
    }

    var measure = start + dIn;
    if (S.hl_enabled && S.hl_reveal && times.length) measure = Math.max(measure, times[times.length - 1][0]);
    measure = Math.min(measure + 0.02, end - dOut - 0.01);
    main.transform.anchorPoint.expression = anchorExpression(perLine, Math.max(measure, start));
    main.transform.position.setValue(basePos);
    main.transform.rotation.setValue(S.rotation);
    if (Math.abs(S.text_opacity - 100) > 0.5) main.transform.opacity.setValue(S.text_opacity);
    if (gradient) addGradient(main);
    if (S.shadow_enabled) addShadow(silhouette);

    var all = [main].concat(dups);
    if (hlLayer) all.push(hlLayer);
    for (var i = 0; i < all.length; i++) setTiming(all[i], start, end);
    if (S.box_enabled) setTiming(last, start, end);
    textAnimators(main, "main", start, dIn, times);
    for (var j = 0; j < dups.length; j++) textAnimators(dups[j], "dup", start, dIn, times);
    if (hlLayer) textAnimators(hlLayer, "hl", start, dIn, times);
    animate(main, start, end, basePos, dIn, dOut, all);
}

app.beginUndoGroup("AE Subs: " + T.comp_name);
try {
    var comp = getComp();
    R.comp = comp.name;
    if (A.remove_old) removeOld(comp);

    for (var n = 0; n < ITEMS.length; n++) {
        var it = ITEMS[n];
        S = STYLES[it.style];
        if (it.start >= comp.duration || it.end <= it.start) { R.skipped++; continue; }
        var end = Math.min(it.end, comp.duration);
        var dIn = S.anim_in == "none" ? 0 : S.anim_in_dur;
        var dOut = S.anim_out == "none" ? 0 : S.anim_out_dur;
        if (dIn + dOut > end - it.start) { var k = (end - it.start) / (dIn + dOut); dIn *= k; dOut *= k; }
        var basePos = [comp.width * S.pos_x / 100, comp.height * S.pos_y / 100];
        var baseName = T.prefix + " " + pad(n + 1);

        if (S.box_enabled && S.box_mode == "lines" && it.lines.length > 1) {
            // своя плашка на каждой строке: каждая строка — отдельный слой
            var lineH = S.auto_leading ? S.font_size * 1.2 : S.leading;
            var nl = it.lines.length;
            var firstBase = S.vertical_anchor == "top" ? basePos[1] + S.font_size * 0.8 :
                            S.vertical_anchor == "center" ? basePos[1] - (nl - 1) * lineH / 2 + S.font_size * 0.35 :
                            basePos[1] - (nl - 1) * lineH - S.font_size * 0.2;
            var wi = 0;
            for (var li = 0; li < nl; li++) {
                var wc = it.lines[li].split(" ").length;
                var lt = it.words.slice(wi, wi + wc);
                wi += wc;
                buildUnit(comp, baseName + "." + (li + 1), it.lines[li], it.start, end, lt,
                          [basePos[0], firstBase + li * lineH], true, dIn, dOut, it.line_ranges[li]);
            }
        } else {
            buildUnit(comp, baseName, it.text, it.start, end, it.words, basePos, false, dIn, dOut, it.ranges);
        }
        R.added++;
    }
    if (R.no_word_styles) R.warnings.push("Эта версия After Effects не умеет стиль отдельных слов (нужна AE 2024 или новее).");
    if (R.range_errors) R.warnings.push("Стиль части слов не применился: " + R.range_error);
    if (!R.font_ok) R.warnings.push("Не все шрифты найдены в After Effects — где-то оставлен стандартный.");
    if (!R.style_ok) R.warnings.push("After Effects отклонил часть настроек текста.");
    R.ok = true;
} catch (err) {
    R.error = err.message == "NO_COMP"
        ? "Не выбрана композиция. Откройте в After Effects композицию с видео (кликните по её таймлайну) и отправьте снова."
        : err.toString() + (err.line ? " (строка " + err.line + ")" : "");
} finally {
    app.endUndoGroup();
}

var msg = R.ok ? "Готово! Добавлено: " + R.added + " в композицию «" + R.comp + "»." : "Ошибка AE Subs: " + R.error;
if (R.warnings.length) msg += "\n\n" + R.warnings.join("\n");
if (!DATA.result_path || !writeResult(DATA.result_path, R, msg)) {
    if (!DATA.result_path) alert(msg, "AE Subs");
}
})();
"""

# ---------------------------------------------------------------- чтение композиции (звук)

JSX_QUERY = r"""
(function () {
var RESULT_PATH = /*RESULT*/;
var R = {ok: false, sources: [], notes: []};

function collect(comp, offset, scale, lo, hi, prefix, depth) {
    if (depth > 6) return;
    for (var i = 1; i <= comp.numLayers; i++) {
        var l = comp.layer(i);
        var hasAudio = false;
        try { hasAudio = l.hasAudio && l.audioEnabled; } catch (e) {}
        var hasVideo = false;
        try { hasVideo = l.hasVideo && l.enabled; } catch (e) {}
        if ((!hasAudio && !(hasVideo && depth == 0)) || !l.source) continue;
        var st = l.stretch / 100;
        if (st <= 0) { R.notes.push("«" + l.name + "»: обратное воспроизведение не поддерживается"); continue; }
        if (l.timeRemapEnabled) R.notes.push("«" + l.name + "»: ремап времени не учитывается");
        // видимая часть слоя во времени главной композиции
        var a = Math.max(offset + scale * l.inPoint, lo);
        var b = Math.min(offset + scale * l.outPoint, hi);
        if (b <= a) continue;
        var src = l.source;
        if (src instanceof CompItem) {
            if (hasAudio) collect(src, offset + scale * l.startTime, scale * st, a, b, prefix + l.name + " › ", depth + 1);
        } else if (src instanceof FootageItem && src.file) {
            R.sources.push({
                name: prefix + l.name, path: src.file.fsName, exists: src.file.exists,
                comp_start: offset + scale * l.startTime, rate: scale * st, a: a, b: b,
                has_video: l.hasVideo, has_audio: hasAudio, depth: depth
            });
        }
    }
}

try {
    var comp = mainComp();
    R.comp = {name: comp.name, width: comp.width, height: comp.height, fps: comp.frameRate,
              duration: comp.duration, work_start: comp.workAreaStart, work_duration: comp.workAreaDuration};
    collect(comp, 0, 1, 0, comp.duration, "", 0);
    R.ok = true;
} catch (e) {
    R.error = e.message == "NO_COMP" ? "no_comp" : e.toString();
}
writeResult(RESULT_PATH, R, R.ok ? "AE Subs: композиция «" + R.comp.name + "» прочитана."
                                 : "AE Subs: откройте композицию в After Effects.");
})();
"""

# ---------------------------------------------------------------- обратная синхронизация

JSX_SYNC = r"""
(function () {
var DATA = /*DATA*/;
var R = {ok: false, items: []};
try {
    var main = mainComp();
    R.main = main.name;
    var comp = main;
    if (DATA.target != "layers") {
        var holder = findHolder(main, DATA.comp_name + " (" + main.name + ")");
        if (!holder) throw new Error("NO_HOLDER");
        comp = holder.source;
    }
    var re = new RegExp("^" + DATA.prefix + " (\\d+)(?:\\.(\\d+))? \\| ");
    for (var i = 1; i <= comp.numLayers; i++) {
        var l = comp.layer(i);
        var m = re.exec(l.name);
        if (!m) continue;
        var tp = l.property("ADBE Text Properties");
        if (!tp) continue;
        var text = String(tp.property("ADBE Text Document").value.text).replace(/\r/g, "\n");
        R.items.push({num: parseInt(m[1], 10), line: m[2] ? parseInt(m[2], 10) : 0, text: text,
                      start: l.inPoint, end: l.outPoint});
    }
    R.ok = true;
} catch (e) {
    R.error = e.message == "NO_COMP" ? "no_comp" : e.message == "NO_HOLDER" ? "no_holder" : e.toString();
}
writeResult(DATA.result_path, R, "AE Subs: синхронизация.");
})();
"""


def _json(data) -> str:
    # ensure_ascii: кириллица уходит как \uXXXX — ExtendScript это понимает
    return json.dumps(data, ensure_ascii=True)


def _style_data(style: Style, font_ps: str) -> dict:
    d = style.to_dict()
    d["font_ps"] = font_ps
    return d


def _ranges(text: str, word_styles: dict, ps_for) -> list[dict]:
    """Позиции слов со своим стилем в тексте: [{"s": начало, "e": конец, "p": отличия стиля}]."""
    out = []
    for i, m in enumerate(re.finditer(r"\S+", text)):
        ov = word_styles.get(i)
        if not ov:
            continue
        p = {k: v for k, v in ov.items() if k in CHAR_KEYS or k == "font_ps"}
        if "font_family" in p or "font_style" in p:
            p.setdefault("font_family", ov.get("font_family"))
            p.setdefault("font_style", ov.get("font_style"))
        out.append({"s": m.start(), "e": m.end(), "p": p, "w": i})
    return out


def build_jsx(items: list[Subtitle], default_style: Style, ae: AESettings, result_path: str = "",
              font_ps=None) -> str:
    """Скрипт вставки субтитров. У субтитра может быть свой стиль и свои стили слов.

    font_ps — функция (family, style) -> PostScript-имя или строка (для общего стиля).
    """
    def ps_for(family: str, style: str) -> str:
        if callable(font_ps):
            return font_ps(family, style)
        return font_ps or ""

    styles, style_index, data_items = [], {}, []
    for s in sorted(items, key=lambda s: s.start):
        st = s.style or default_style
        key = json.dumps(st.to_dict(), sort_keys=True)
        if key not in style_index:
            style_index[key] = len(styles)
            styles.append(_style_data(st, ps_for(st.font_family, st.font_style)))
        text = wrap_text(s.text, st.max_chars).replace("\r\n", "\n")
        if not text.strip():
            continue
        lines = text.split("\n")
        # стили слов: позиции во всём тексте и отдельно в каждой строке (для плашки по строкам)
        ws = {k: dict(v) for k, v in s.word_styles.items()}
        for v in ws.values():
            if "font_family" in v or "font_style" in v:
                fam = v.get("font_family", st.font_family)
                sty = v.get("font_style", st.font_style)
                v.update(font_family=fam, font_style=sty, font_ps=ps_for(fam, sty))
        ranges = _ranges(text, ws, ps_for)
        line_ranges, offset = [], 0
        for line in lines:
            n = len(line.split())
            local = {k - offset: v for k, v in ws.items() if offset <= k < offset + n}
            line_ranges.append(_ranges(line, local, ps_for))
            offset += n
        off = ae.time_offset
        data_items.append({
            "start": round(s.start + off, 4), "end": round(s.end + off, 4),
            "text": text.replace("\n", "\r"), "lines": lines,
            "words": [[round(a + off, 3), round(b + off, 3)] for a, b in s.word_times()],
            "style": style_index[key], "ranges": ranges, "line_ranges": line_ranges,
        })
    data = {
        "styles": styles, "items": data_items, "ae": ae.to_dict(),
        "track": {"prefix": ae.layer_prefix, "comp_name": ae.comp_name},
        "result_path": result_path.replace("\\", "/"),
    }
    return JSX_COMMON + JSX_APPLY.replace("/*DATA*/", _json(data), 1)


def build_query_jsx(result_path: str) -> str:
    return JSX_COMMON + JSX_QUERY.replace("/*RESULT*/", _json(result_path.replace("\\", "/")), 1)


def build_sync_jsx(ae: AESettings, result_path: str) -> str:
    data = {"target": ae.target, "result_path": result_path.replace("\\", "/"),
            "prefix": ae.layer_prefix, "comp_name": ae.comp_name}
    return JSX_COMMON + JSX_SYNC.replace("/*DATA*/", _json(data), 1)
