"""비교·점검 결과 → 한 파일 HTML(외부 자원 없음, 표 + 인라인 SVG, 라이트·다크).

- 입력: comparison@1, comparison-set@1(층마다 하나), result@1(점검 결과 하나).
- 등급(L0·L1·L2·L3-E 에뮬레이터·L3-D 실기기)을 머리와 절마다 표시한다. 한 표에는 한 층·한 등급만 둔다.
- 에뮬레이터(L3-E)에서 통계로 개선이면 「실기기 확인 대기」 꼬리표.
- 색은 테마 CSS의 변수만 쓴다. 테마: 프로젝트 프로필 `[report] css`(파일 경로) → 없으면 저장소 기본 테마.
- 아래에 환경·매니페스트 해시·측정 코드 해시·회차 수·무효 회차를 적는다.
"""

from __future__ import annotations

import html
import math
from pathlib import Path

PKG = Path(__file__).resolve().parent
CLASS_LABEL = {"L0": "L0 정적", "L1": "L1 빌드 산출물", "L2": "L2 에디터 Play",
               "L3-E": "L3-E 에뮬레이터", "L3-D": "L3-D 실기기", "console": "스토어 콘솔"}
OUTCOME_KO = {"pass": "통과", "improved": "개선", "warn": "경고", "fail": "실패", "review": "확인 필요",
              "pending": "판단 보류", "record": "기록만", "not_collected": "수집 못 함"}
ORDER = ["fail", "warn", "review", "not_collected", "pending", "improved", "pass", "record"]

STRUCT_CSS = """
.lvl { display: inline-block; font-size: 13px; font-weight: 600; padding: 2px 10px; border-radius: 999px;
  background: var(--accent); color: var(--on-accent); margin-right: 6px; }
.badges { margin: 0 0 6px; display: flex; flex-wrap: wrap; gap: 6px; }
.lvl.soft { background: var(--accent-soft); color: var(--text); }
.v { white-space: nowrap; font-weight: 600; }
.v-better { color: var(--ok); }
.v-worse { color: var(--text); text-decoration: underline; text-decoration-thickness: 2px; text-underline-offset: 3px; }
.v-muted { color: var(--muted); font-weight: 400; }
.wait { display: inline-block; font-size: 12px; padding: 1px 8px; border-radius: 999px;
  border: 1px solid var(--accent); color: var(--accent); white-space: nowrap; }
.dist svg, .forest svg { display: block; width: 100%; height: auto; max-width: 100%; }
td.dist { min-width: 150px; width: 180px; }
td.name { min-width: 14em; }
td.name code { white-space: nowrap; }
.forest { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin: 10px 0; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13px; color: var(--muted); margin: 6px 0; }
.legend i { display: inline-block; width: 14px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: middle; }
.small { font-size: 13px; color: var(--muted); }
dl.kv { display: grid; grid-template-columns: minmax(8em, max-content) minmax(0, 1fr); gap: 4px 14px; margin: 8px 0; font-size: 14px; }
dl.kv dt { color: var(--muted); }
dl.kv dd { margin: 0; overflow-wrap: anywhere; }
"""


def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return "-"
        a = abs(v)
        if a >= 10000:
            return f"{v:,.0f}"
        if a >= 100:
            return f"{v:.1f}"
        if a >= 1:
            return f"{v:.2f}"
        return f"{v:.3g}"
    if isinstance(v, list):
        return ", ".join(fmt(x) for x in v[:12]) + (" …" if len(v) > 12 else "")
    if isinstance(v, dict):
        return "{…}"
    return str(v)


def pct(v, signed=True) -> str:
    if v is None:
        return "-"
    return f"{v:+.2f}%" if signed else f"{v:.2f}%"


def theme_css(css_path: str | Path | None = None) -> str:
    if css_path is None:
        try:
            from perfkit.config import settings

            s = settings()
            c = (s.profile.get("report") or {}).get("css") if s.profile else None
            if c:
                p = Path(c)
                if not p.is_absolute() and s.profile_path is not None:
                    p = s.profile_path.parent / p
                css_path = p
        except Exception:
            css_path = None
    if css_path is not None and Path(css_path).is_file():
        return Path(css_path).read_text(encoding="utf-8")
    return (PKG / "default.css").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- SVG

def strip_svg(base: list, cand: list, unit: str = "") -> str:
    """기준·후보 회차 값 점 그림(같은 축), 중앙값 세로선."""
    vals = [float(x) for x in base + cand if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        lo, hi = lo - (abs(lo) * 0.01 or 1), hi + (abs(hi) * 0.01 or 1)
    W, H, L, Rm = 200, 46, 30, 6

    def x(v):
        return L + (float(v) - lo) / (hi - lo) * (W - L - Rm)

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="기준·후보 회차 값 분포">']
    for row, (name, vs, fill) in enumerate((("기준", base, "var(--bar-before)"), ("후보", cand, "var(--bar-after)"))):
        y = 12 + row * 18
        parts.append(f'<text x="0" y="{y + 4}" font-size="10" fill="var(--muted)">{name}</text>')
        parts.append(f'<line x1="{L}" y1="{y}" x2="{W - Rm}" y2="{y}" stroke="var(--border)" stroke-width="1"/>')
        nums = sorted(float(v) for v in vs if isinstance(v, (int, float)) and not isinstance(v, bool))
        for v in nums:
            parts.append(f'<circle cx="{x(v):.1f}" cy="{y}" r="3.2" fill="{fill}"/>')
        if nums:
            m = nums[len(nums) // 2] if len(nums) % 2 else (nums[len(nums) // 2 - 1] + nums[len(nums) // 2]) / 2
            parts.append(f'<line x1="{x(m):.1f}" y1="{y - 7}" x2="{x(m):.1f}" y2="{y + 7}" stroke="var(--text)" stroke-width="1.5"/>')
    parts.append(f'<text x="{L}" y="{H - 1}" font-size="9" fill="var(--muted)">{esc(fmt(lo))}</text>')
    parts.append(f'<text x="{W - Rm}" y="{H - 1}" font-size="9" fill="var(--muted)" text-anchor="end">{esc(fmt(hi))}{esc(unit)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def forest_svg(rows: list[dict]) -> str:
    """지표마다 변화(HL %)와 95% 구간, 동등 범위 띠, 0선."""
    rows = [r for r in rows if r.get("ci") and None not in r["ci"] and r.get("hl_pct") is not None]
    if not rows:
        return ""
    ext = max(max(abs(r["ci"][0]), abs(r["ci"][1]), abs(r["hl_pct"]), r.get("eq") or 0) for r in rows)
    ext = max(1.0, min(ext * 1.1, 200.0))
    W, row_h, L, Rm, top = 760, 22, 230, 70, 18
    H = top + row_h * len(rows) + 22

    def x(v):
        v = max(-ext, min(ext, v))
        return L + (v + ext) / (2 * ext) * (W - L - Rm)

    p = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="지표별 변화와 95% 구간">']
    p.append(f'<line x1="{x(0):.1f}" y1="{top - 6}" x2="{x(0):.1f}" y2="{H - 18}" stroke="var(--muted)" stroke-width="1"/>')
    for t in (-ext, -ext / 2, ext / 2, ext):
        p.append(f'<text x="{x(t):.1f}" y="{H - 4}" font-size="10" fill="var(--muted)" text-anchor="middle">{t:+.0f}%</text>')
    p.append(f'<text x="{x(0):.1f}" y="{H - 4}" font-size="10" fill="var(--muted)" text-anchor="middle">0</text>')
    for i, r in enumerate(rows):
        y = top + i * row_h + row_h / 2
        if r.get("eq"):
            p.append(f'<rect x="{x(-r["eq"]):.1f}" y="{y - 8:.1f}" width="{x(r["eq"]) - x(-r["eq"]):.1f}" height="16" '
                     f'fill="var(--accent-soft)"/>')
        p.append(f'<text x="0" y="{y + 4:.1f}" font-size="11" fill="var(--text)">{esc(r["name"][:34])}</text>')
        lo, hi = r["ci"]
        p.append(f'<line x1="{x(lo):.1f}" y1="{y:.1f}" x2="{x(hi):.1f}" y2="{y:.1f}" stroke="var(--text)" stroke-width="2"/>')
        fill = "var(--accent)" if r.get("verdict") == "different" else "var(--bg)"
        p.append(f'<circle cx="{x(r["hl_pct"]):.1f}" cy="{y:.1f}" r="4.5" fill="{fill}" stroke="var(--accent)" stroke-width="2"/>')
        p.append(f'<text x="{W - 2}" y="{y + 4:.1f}" font-size="11" fill="var(--muted)" text-anchor="end">{esc(r["label"])}</text>')
    p.append("</svg>")
    return "".join(p)


# --------------------------------------------------------------------------- 절

def _class_badge(layer: str, soft=False) -> str:
    return f'<span class="lvl{" soft" if soft else ""}">{esc(CLASS_LABEL.get(layer, layer))}</span>'


def _verdict_html(st: dict, m: dict) -> str:
    from perfkit.compare.judge import stat_label

    if m.get("excluded"):
        return '<span class="v v-muted">판정 제외</span>'
    if not st:
        return '<span class="v v-muted">-</span>'
    lab = stat_label(st)
    if st.get("info_only"):
        return f'<span class="v v-muted">{esc(lab)} (참고)</span>'
    cls = {"better": "v-better", "worse": "v-worse"}.get(st.get("direction"), "v-muted")
    return f'<span class="v {cls}">{esc(lab)}</span>'


def _abab_section(p: dict, manifest) -> str:
    layer = p["layer"]
    s = p["summary"]
    out = [f'<section id="{esc(layer)}"><h2>{_class_badge(layer)} 회차 묶음 비교</h2>']
    out.append(f'<p class="small">기준 <b>{esc(p["base"]["label"])}</b> {s["n_base"]}회 · 후보 <b>{esc(p["cand"]["label"])}</b> '
               f'{s["n_cand"]}회 · 무효로 뺀 회차 {s["n_invalid_runs"]}회 · '
               f'{"쌍 비교(라운드별 " + str(s["pairs"]) + "쌍, Wilcoxon)" if s["paired"] else "독립 비교(Mann-Whitney)"} · '
               f'{"교차(ABAB) 측정" if s["interleaved"] else "교차 아님"}</p>')
    if not p.get("valid", True):
        out.append('<div class="note"><b>이 비교는 무효입니다.</b><ul>' +
                   "".join(f"<li>{esc(r)}</li>" for r in s.get("invalid_reasons", [])) + "</ul></div>")
    if s.get("warnings"):
        out.append('<div class="note"><ul>' + "".join(f"<li>{esc(w)}</li>" for w in s["warnings"]) + "</ul></div>")
    rows, excluded, forest = [], [], []
    for iid, it in p["items"].items():
        title = (manifest.get(iid) or {}).get("title", "") if manifest else ""
        for k, m in it["metrics"].items():
            st = m.get("stat") or {}
            unit = ""
            if manifest and manifest.get(iid):
                ms = {x["key"]: x for x in manifest.metrics_of(manifest.get(iid))}
                unit = (ms.get(k) or {}).get("unit") or ""
            name = f"{iid}.{k}"
            if m.get("excluded") or (m["outcome"] == "record" and not st):
                excluded.append((iid, k, title, m))
                continue
            wait = (it.get("needs_device_confirm") and st.get("direction") == "better" and not st.get("info_only"))
            wait_html = ' <span class="wait">실기기 확인 대기</span>'
            ci = st.get("ci")
            ci_s = (f"[{ci[0]:+.2f}, {ci[1]:+.2f}]{'%' if st.get('ci_unit') == 'pct' else ''}"
                    if ci and None not in ci else "-")
            rows.append(
                f"<tr><td class='name'><b>{esc(iid)}</b> <code>{esc(k)}</code><br><span class='small'>{esc(title[:48])}</span></td>"
                f"<td class='num'>{esc(fmt(st.get('base_median')))}</td><td class='num'>{esc(fmt(st.get('cand_median')))}</td>"
                f"<td class='num'>{esc(pct(st.get('hl_pct')))}</td><td class='num'>{esc(ci_s)}</td>"
                f"<td class='num'>{esc(fmt(st.get('cliffs_delta')))}</td><td class='num'>{esc(fmt(st.get('p')))}</td>"
                f"<td>{_verdict_html(st, m)}{wait_html if wait else ''}"
                f"<br><span class='small'>{esc((st.get('reasons') or [''])[0][:90])}</span></td>"
                f"<td>{esc(OUTCOME_KO.get(m['outcome'], m['outcome']))}<br><span class='small'>잡음 {esc(m.get('noise'))}</span></td>"
                f"<td class='dist'>{strip_svg(m.get('base') or [], m.get('cand') or [], unit)}</td></tr>")
            if st and not st.get("info_only") and st.get("ci_unit") == "pct":
                from perfkit.compare.judge import stat_label

                forest.append({"name": name, "ci": st.get("ci"), "hl_pct": st.get("hl_pct"),
                               "eq": st.get("equivalence_pct"), "verdict": st.get("outcome"), "label": stat_label(st)})
    if forest:
        out.append("<h3>변화와 95% 구간</h3>")
        out.append('<div class="legend"><span><i style="background:var(--accent)"></i>다름(구간이 0을 안 품음)</span>'
                   '<span><i style="background:var(--accent-soft)"></i>동등 범위(같음 판정 띠)</span>'
                   '<span>가로선 = 부트스트랩 95% 구간, 점 = HL 이동량(기준 중앙값 대비)</span></div>')
        out.append(f'<div class="forest">{forest_svg(forest)}</div>')
    out.append("<h3>지표별 판정</h3>")
    out.append('<div class="tbl"><table><thead><tr><th>항목·지표</th><th class="num">기준 중앙값</th><th class="num">후보 중앙값</th>'
               '<th class="num">변화(HL)</th><th class="num">95% 구간</th><th class="num">Cliff δ</th><th class="num">p</th>'
               '<th>통계 판정</th><th>판정 규칙</th><th>회차 값</th></tr></thead><tbody>'
               + "".join(rows) + "</tbody></table></div>")
    if excluded:
        out.append("<h3>판정하지 않은 지표(값만)</h3>")
        out.append('<div class="tbl"><table><thead><tr><th>항목·지표</th><th>이유</th><th>기준 값</th><th>후보 값</th></tr></thead><tbody>'
                   + "".join(f"<tr><td><b>{esc(i)}</b> <code>{esc(k)}</code></td><td>{esc('; '.join(m.get('reasons') or []))}</td>"
                             f"<td>{esc(fmt(m.get('base')))}</td><td>{esc(fmt(m.get('cand')))}</td></tr>"
                             for i, k, _, m in excluded) + "</tbody></table></div>")
    out.append("</section>")
    return "".join(out)


def _diff_section(p: dict, manifest) -> str:
    layer = p["layer"]
    s = p["summary"]
    out = [f'<section id="{esc(layer)}"><h2>{_class_badge(layer)} 결정적 비교(diff)</h2>']
    out.append(f'<p class="small">기준 <b>{esc(p["base"]["label"])}</b> · 후보 <b>{esc(p["cand"]["label"])}</b> · '
               f'바뀐 항목 {esc(s.get("changes", "-"))} · 새 위반 {esc(s.get("new_violations", 0))} · '
               f'없어진 위반 {esc(s.get("removed_violations", 0))}</p>')
    for w in s.get("warnings", []):
        out.append(f'<div class="note">{esc(w)}</div>')
    rows = []
    items = sorted(p["items"].items(), key=lambda kv: (ORDER.index(kv[1]["outcome"]) if kv[1]["outcome"] in ORDER else 99, kv[0]))
    for iid, it in items:
        if it["outcome"] in ("pass", "record") and not it.get("changed"):
            continue
        title = (manifest.get(iid) or {}).get("title", "") if manifest else ""
        ms = []
        for k, m in (it.get("metrics") or {}).items():
            if m.get("delta") not in (None, 0) or m["outcome"] not in ("pass", "record"):
                b, c = m.get("base"), m.get("cand")
                ms.append(f"<code>{esc(k)}</code> {esc(fmt(b))} → {esc(fmt(c))}"
                          + (f" ({esc(pct(m.get('delta_pct')))})" if m.get("delta_pct") is not None else ""))
        v = it.get("violations")
        vtxt = f"+{v['new_count']} / −{v['removed_count']}" if v else ""
        rows.append(f"<tr><td><b>{esc(iid)}</b><br><span class='small'>{esc(title[:60])}</span></td>"
                    f"<td>{esc(OUTCOME_KO.get(it['outcome'], it['outcome']))}</td><td>{'<br>'.join(ms) or '-'}</td>"
                    f"<td class='num'>{esc(vtxt)}</td></tr>")
    if rows:
        out.append('<div class="tbl"><table><thead><tr><th>항목</th><th>판정</th><th>지표 변화</th><th class="num">위반 +새/−없어짐</th>'
                   '</tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")
    else:
        out.append('<div class="okbox">바뀐 항목이 없습니다.</div>')
    out.append("</section>")
    return "".join(out)


def _env_section(parts: list[dict]) -> str:
    out = ['<section id="env"><h2>환경과 해시</h2>']
    for p in parts:
        kv = [("층·등급", CLASS_LABEL.get(p["layer"], p["layer"])),
              ("매니페스트", f'{p.get("manifest_version")} · {p.get("manifest_sha")}'),
              ("프로젝트 프로필", f'{p.get("profile_name") or "-"} · {p.get("profile_sha") or "-"}'),
              ("방법", p.get("method"))]
        if p["method"] != "diff":
            env = p.get("env") or {}
            st = p.get("stat") or {}
            kv += [("측정 코드 해시", env.get("probe_hash")), ("품질 설정 해시", env.get("quality_hash")),
                   ("시나리오·세이브·씨", f'{env.get("scenario")} · {env.get("save")} · {env.get("seed")}'),
                   ("기기", f'{env.get("device_profile")} · {env.get("os_build")}')]
            if env.get("emu"):
                e = env["emu"]
                kv.append(("에뮬레이터", ", ".join(f"{k}={v}" for k, v in e.items())))
            if env.get("host"):
                kv.append(("호스트", ", ".join(f"{k}={v}" for k, v in env["host"].items())))
            kv += [("통계", f'부트스트랩 {st.get("bootstrap")}회 · 씨 {st.get("seed")} · 유의수준 {st.get("alpha")} · '
                          f'최소 회차 {st.get("min_runs")} · 쌍 {st.get("pairing")} · 잡음 바닥 {st.get("noise_floor")}')]
            for side in ("base", "cand"):
                sd = p[side]
                kv.append((("기준" if side == "base" else "후보") + " 회차",
                           f'{sd.get("n_runs")}회 중 {sd.get("n_used")}회 사용, 무효 {sd.get("n_invalid")}회 · '
                           f'빌드 {sd.get("build_hash") or "-"} · 커밋 {sd.get("commit") or "-"} · '
                           f'{sd.get("started_first", "")} ~ {sd.get("started_last", "")}'))
                for b in sd.get("invalid_runs", []):
                    kv.append(("  무효", f'{b["run_id"]}: {b["reason"]}'))
        else:
            for side in ("base", "cand"):
                sd = p[side]
                kv.append((("기준" if side == "base" else "후보"),
                           f'{sd.get("label")} · {", ".join(sd.get("result_refs", []))} · 커밋 {sd.get("commit") or "-"}'))
        out.append(f"<h3>{_class_badge(p['layer'], soft=True)}</h3><dl class='kv'>" +
                   "".join(f"<dt>{esc(k)}</dt><dd><code>{esc(v)}</code></dd>" if "해시" in k or k == "매니페스트"
                           else f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in kv) + "</dl>")
    out.append("</section>")
    return "".join(out)


def _result_section(r: dict, manifest) -> str:
    layer = r["layer"]
    rows = []
    for iid, ent in sorted(r["items"].items()):
        title = (manifest.get(iid) or {}).get("title", "") if manifest else ""
        if ent.get("status") == "ok":
            vals = "<br>".join(f"<code>{esc(k)}</code> {esc(fmt(v))}" for k, v in (ent.get("values") or {}).items())
            st = "값"
        else:
            vals, st = esc(ent.get("reason", "")), esc(ent.get("status"))
        rows.append(f"<tr><td><b>{esc(iid)}</b><br><span class='small'>{esc(title[:60])}</span></td><td>{st}</td><td>{vals}</td></tr>")
    return (f'<section id="{esc(layer)}"><h2>{_class_badge(layer)} 점검 결과</h2>'
            f'<p class="small">{esc((r.get("source") or {}).get("ref"))} · 수집 {esc(r.get("collected_at"))}</p>'
            '<div class="tbl"><table><thead><tr><th>항목</th><th>상태</th><th>값</th></tr></thead><tbody>'
            + "".join(rows) + "</tbody></table></div></section>")


def render(doc: dict, *, css_path: str | Path | None = None, title: str | None = None) -> str:
    try:
        from perfkit.manifest import load_effective

        manifest = load_effective()
    except Exception:
        manifest = None
    schema = doc.get("schema")
    if schema == "perfkit/result@1":
        parts, kind = [doc], "result"
    elif schema == "perfkit/comparison-set@1":
        parts, kind = list(doc["parts"]), "compare"
    elif schema == "perfkit/comparison@1":
        parts, kind = [doc], "compare"
    else:
        raise ValueError(f"보고할 수 없는 문서: {schema}")
    layers = [p["layer"] for p in parts]
    if kind == "result":
        h1 = title or f"성능 점검 결과 — {CLASS_LABEL.get(layers[0], layers[0])}"
        body = _result_section(doc, manifest)
        cards = ""
        meta = f"<span>{esc(doc.get('collected_at'))}</span>"
        banner = ""
    else:
        bl = doc.get("base_label") or parts[0]["base"]["label"]
        cl = doc.get("cand_label") or parts[0]["cand"]["label"]
        h1 = title or f"성능 비교 — {bl} → {cl}"
        valid = all(p.get("valid", True) for p in parts)
        banner = "" if valid else '<div class="note"><b>무효인 비교가 있습니다.</b> 아래 절의 사유를 보세요. 판정을 쓰지 않습니다.</div>'
        sc: dict = {}
        for p in parts:
            for k, v in (p["summary"].get("stat_counts") or {}).items():
                sc[k] = sc.get(k, 0) + v
            if p["method"] == "diff":
                for k, v in p["summary"]["counts"].items():
                    sc["diff_" + k] = sc.get("diff_" + k, 0) + v
        card_defs = [("different_better", "다름·개선"), ("different_worse", "다름·악화"), ("same", "같음"),
                     ("inconclusive", "판단 보류"), ("excluded", "판정 제외"), ("diff_fail", "diff 실패"),
                     ("diff_warn", "diff 경고"), ("diff_improved", "diff 개선")]
        cards = '<div class="cards">' + "".join(
            f'<div class="card"><b>{sc.get(k, 0)}</b><small>{esc(lab)}</small></div>'
            for k, lab in card_defs if sc.get(k) or k in ("different_better", "different_worse", "same", "inconclusive")) + "</div>"
        meta = f"<span>{esc(doc.get('created_at') or parts[0].get('created_at'))}</span>"
        body = "".join(_diff_section(p, manifest) if p["method"] == "diff" else _abab_section(p, manifest) for p in parts)
    badges = "".join(_class_badge(l) for l in layers)
    css = theme_css(css_path)
    foot = _env_section(parts) if kind == "compare" else ""
    return ("<!DOCTYPE html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{esc(h1)}</title><style>{css}\n{STRUCT_CSS}</style></head><body><div class=\"wrap\">"
            f"<header><h1>{esc(h1)}</h1><div class=\"badges\">{badges}</div><div class=\"meta\">{meta}</div></header>{banner}{cards}"
            f"{body}{foot}<footer>perfkit report · 등급이 다른 수치는 한 표에 섞지 않습니다. "
            "에뮬레이터 개선은 실기기 회차로 재현되기 전까지 「실기기 확인 대기」입니다.</footer></div></body></html>\n")


def render_to(doc: dict, path: str | Path, **kw) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render(doc, **kw), encoding="utf-8", newline="\n")
    return p
