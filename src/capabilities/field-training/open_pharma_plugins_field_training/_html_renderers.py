"""Self-contained, source-ledger HTML renderers for Field Training outputs."""

from __future__ import annotations

from html import escape

_CSS = """\
:root {
  --ink:#102a43;--navy:#0b1f33;--teal:#0f766e;--teal-soft:#dff3f1;
  --coral:#b83a3a;--coral-soft:#fde8e7;--paper:#f7f9fc;--white:#fff;
  --slate:#526778;--line:#d8e1e8;--focus:#f4b942;
  --shadow:0 18px 55px rgba(11,31,51,.12);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;color:var(--ink);background:linear-gradient(135deg,#e9f0f5 0,var(--paper) 42%,#edf6f5 100%);font-family:"Avenir Next",Avenir,"Segoe UI",sans-serif;font-size:16px;line-height:1.65}
button,input{font:inherit}button{cursor:pointer}a{color:inherit}
.skip-link{position:fixed;left:1rem;top:-5rem;z-index:100;padding:.7rem 1rem;color:var(--white);background:var(--navy);border-radius:.35rem}.skip-link:focus{top:1rem}
.dossier-shell{display:grid;grid-template-columns:260px minmax(0,1fr);width:min(1280px,calc(100% - 2rem));margin:1rem auto;overflow:clip;background:var(--white);border:1px solid rgba(16,42,67,.12);border-radius:18px;box-shadow:var(--shadow)}
.dossier-rail{position:relative;padding:2rem 1.35rem;color:#dce8f1;background:var(--navy)}
.rail-mark{display:grid;grid-template-columns:4px 1fr;gap:.75rem;align-items:stretch;margin-bottom:2.75rem}.rail-mark::before{content:"";background:var(--teal);border-radius:99px}
.rail-kicker,.eyebrow{font-family:"SFMono-Regular",Consolas,monospace;font-size:.72rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase}.rail-title{margin-top:.25rem;color:var(--white);font-size:1rem;font-weight:700}
.dossier-nav{position:sticky;top:1.5rem}.dossier-nav ul{margin:0;padding:0;list-style:none}.dossier-nav li+li{margin-top:.3rem}.dossier-nav a{display:block;padding:.6rem .7rem;color:#c7d7e4;text-decoration:none;border-left:2px solid transparent;border-radius:0 .35rem .35rem 0}.dossier-nav a:hover,.dossier-nav a:focus-visible{color:var(--white);background:rgba(255,255,255,.08);border-left-color:#53c5b9}
.dossier-workspace{min-width:0}.utility-bar{display:flex;justify-content:space-between;gap:1rem;align-items:center;padding:.8rem clamp(1.25rem,4vw,3.5rem);border-bottom:1px solid var(--line);background:rgba(255,255,255,.94)}
.draft-state{display:inline-flex;gap:.45rem;align-items:center;color:#7f1d1d;font-size:.78rem;font-weight:800;letter-spacing:.04em;text-transform:uppercase}.draft-state::before{content:"";width:.55rem;height:.55rem;background:var(--coral);border-radius:50%;box-shadow:0 0 0 4px var(--coral-soft)}
.utility-actions{display:flex;flex-wrap:wrap;gap:.5rem}.action-button,.filter-button{padding:.5rem .78rem;color:var(--ink);background:var(--white);border:1px solid var(--line);border-radius:.4rem;font-size:.82rem;font-weight:700}.action-button:hover,.filter-button:hover,.filter-button[aria-pressed="true"]{color:var(--white);background:var(--teal);border-color:var(--teal)}
.dossier-main{padding:clamp(1.5rem,4vw,3.5rem)}.dossier-hero{position:relative;margin-bottom:2.5rem;padding:clamp(1.5rem,4vw,3rem);overflow:hidden;color:var(--white);background:var(--navy);border-radius:14px}.dossier-hero::after{content:"SOURCE / MESSAGE / PRACTICE";position:absolute;right:-1.5rem;bottom:1rem;color:rgba(255,255,255,.07);font-family:"SFMono-Regular",Consolas,monospace;font-size:clamp(1.2rem,4vw,3rem);font-weight:800;letter-spacing:.08em;transform:rotate(-4deg)}
.eyebrow{margin:0 0 .75rem;color:#76d6cc}h1,h2,h3{color:var(--navy);line-height:1.2}h1{position:relative;z-index:1;max-width:780px;margin:0;color:var(--white);font-family:Georgia,"Times New Roman",serif;font-size:clamp(2rem,5vw,4.25rem);font-weight:500;letter-spacing:-.035em}h2{margin:0 0 1rem;font-family:Georgia,"Times New Roman",serif;font-size:clamp(1.55rem,3vw,2.3rem)}h3{margin:1.6rem 0 .8rem;font-size:1.05rem;letter-spacing:.01em}p{margin:0 0 .9rem}ul,ol{padding-left:1.3rem}
.hero-meta{display:flex;flex-wrap:wrap;gap:.7rem 1.3rem;margin-top:1.6rem;color:#dce8f1;font-size:.86rem}.section-block{margin-top:3rem;scroll-margin-top:1.5rem}.section-heading{display:grid;grid-template-columns:auto 1fr;gap:.8rem;align-items:center}.section-index{display:inline-grid;width:2.2rem;height:2.2rem;place-items:center;color:var(--white);background:var(--teal);border-radius:50%;font-family:"SFMono-Regular",Consolas,monospace;font-size:.76rem;font-weight:800}
.module-meta,.filter-bar{display:flex;flex-wrap:wrap;gap:.55rem;align-items:center}.module-meta{margin-bottom:1.2rem;color:var(--slate);font-size:.9rem}.filter-bar{margin:1rem 0;padding:.8rem;background:var(--paper);border:1px solid var(--line);border-radius:.6rem}.filter-search{min-width:min(100%,220px);flex:1;padding:.55rem .75rem;border:1px solid var(--line);border-radius:.4rem}
.card-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:.9rem}.card{position:relative;padding:1.15rem;background:var(--white);border:1px solid var(--line);border-radius:.75rem;box-shadow:0 8px 24px rgba(16,42,67,.06)}.card[data-category="safety"]{border-top:3px solid var(--coral)}.card[data-category="efficacy"]{border-top:3px solid var(--teal)}.card-header{margin-bottom:.55rem;color:var(--navy);font-weight:800}
.badge{display:inline-flex;padding:.2rem .55rem;border-radius:99px;font-size:.72rem;font-weight:800;letter-spacing:.04em;text-transform:uppercase}.badge-green{color:#07534d;background:var(--teal-soft)}.badge-yellow{color:#6d4b00;background:#fff2c7}.badge-red{color:#8a2222;background:var(--coral-soft)}.badge-blue{color:#174a67;background:#e0f0f8}.badge-gray{color:#425466;background:#e9eef2}
.source-stack{margin-top:.8rem}.source-disclosure{margin-top:.45rem;overflow:hidden;background:#f3f8fa;border-left:3px solid var(--teal);border-radius:0 .45rem .45rem 0}.source-disclosure summary{padding:.55rem .7rem;color:#315064;cursor:pointer;font-size:.78rem;font-weight:800}.source-disclosure blockquote{margin:0;padding:0 .8rem .75rem;color:var(--slate);font-family:Georgia,serif;font-size:.86rem}
.facilitator-note{padding:.85rem;background:#fff8dd;border:1px dashed #b68a18;border-radius:.5rem}.rubric-weight{color:var(--teal);font-family:"SFMono-Regular",Consolas,monospace;font-weight:800}.meta{margin-top:3rem;padding-top:1rem;color:var(--slate);border-top:1px solid var(--line);font-size:.78rem}
[hidden],.is-filtered{display:none!important}:focus-visible{outline:3px solid var(--focus);outline-offset:3px}
@media (max-width:820px){.dossier-shell{display:block;width:100%;margin:0;border:0;border-radius:0}.dossier-rail{padding:1rem 1.25rem}.rail-mark{margin-bottom:.75rem}.dossier-nav{position:static;overflow-x:auto}.dossier-nav ul{display:flex;gap:.25rem}.dossier-nav a{white-space:nowrap;border-left:0;border-bottom:2px solid transparent}}
@media (max-width:560px){.utility-bar{align-items:stretch;flex-direction:column;padding:.75rem 1rem}.draft-state{align-self:flex-start;white-space:nowrap}.utility-actions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))}.action-button{padding:.48rem .25rem;font-size:.68rem}.dossier-main{padding:1rem}.dossier-hero{padding:1.25rem}.hero-meta{display:grid;gap:.25rem}}
@media print{body{background:#fff;font-size:10.5pt}.dossier-shell{display:block;width:100%;margin:0;border:0;box-shadow:none}.dossier-rail,.utility-bar,.filter-bar{display:none!important}.dossier-main{padding:0}.dossier-hero{color:#000;background:#fff;border:2px solid #000}.dossier-hero h1{color:#000}.source-disclosure{break-inside:avoid}.source-disclosure[open] blockquote,.source-disclosure:not([open])>blockquote{display:block}}
@media (prefers-reduced-motion: reduce){html{scroll-behavior:auto}*,*::before,*::after{transition-duration:.01ms!important;animation-duration:.01ms!important}}
"""


_JS = """\
(() => {
  const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const applyFilters = (scope) => {
    const selected = scope.querySelector('[data-action="filter"][aria-pressed="true"]');
    const category = selected?.dataset.filter || 'all';
    const query = (scope.querySelector('[data-action="search"]')?.value || '').trim().toLowerCase();
    all('[data-category]', scope).forEach((card) => {
      const categoryMatch = category === 'all' || card.dataset.category === category;
      const queryMatch = !query || card.textContent.toLowerCase().includes(query);
      card.classList.toggle('is-filtered', !(categoryMatch && queryMatch));
    });
  };
  document.addEventListener('click', (event) => {
    const control = event.target.closest('[data-action]');
    if (!control) return;
    const action = control.dataset.action;
    if (action === 'print') window.print();
    if (action === 'filter') {
      const scope = control.closest('[data-filter-scope]');
      if (!scope) return;
      all('[data-action="filter"]', scope).forEach((button) => {
        button.setAttribute('aria-pressed', String(button === control));
      });
      applyFilters(scope);
    }
    if (action === 'sources') {
      const open = control.getAttribute('aria-pressed') !== 'true';
      all('.source-disclosure').forEach((item) => { item.open = open; });
      control.setAttribute('aria-pressed', String(open));
      control.textContent = open ? 'Collapse sources' : 'Expand sources';
    }
    if (action === 'facilitator-mode') {
      const enabled = control.getAttribute('aria-pressed') !== 'true';
      control.setAttribute('aria-pressed', String(enabled));
      all('[data-facilitator]').forEach((item) => { item.hidden = !enabled; });
    }
    if (action === 'toggle-answer') {
      const panel = document.getElementById(control.getAttribute('aria-controls'));
      if (panel) {
        panel.hidden = !panel.hidden;
        control.setAttribute('aria-expanded', String(!panel.hidden));
        control.textContent = panel.hidden ? 'Reveal answer' : 'Hide answer';
      }
    }
  });
  document.addEventListener('input', (event) => {
    if (event.target.matches('[data-action="search"]')) {
      const scope = event.target.closest('[data-filter-scope]');
      if (scope) applyFilters(scope);
    }
  });
})();
"""


def _esc(value: object) -> str:
    return escape(str(value))


def _badge_class(value: str) -> str:
    return {
        "efficacy": "badge-green",
        "safety": "badge-red",
        "dosing": "badge-blue",
        "easy": "badge-green",
        "medium": "badge-yellow",
        "hard": "badge-red",
    }.get(value.lower(), "badge-gray")


def _source_html(source: dict) -> str:
    return (
        '<details class="source-disclosure">'
        f"<summary>{_esc(source.get('document_name', ''))} / page or slide "
        f"{_esc(source.get('page_number', '?'))}</summary>"
        f"<blockquote>“{_esc(source.get('excerpt', ''))}”</blockquote>"
        "</details>"
    )


def _sources_html(sources: list[dict]) -> str:
    if not sources:
        return ""
    return f'<div class="source-stack">{"".join(_source_html(source) for source in sources)}</div>'


def _hero(title: str, eyebrow: str, facts: list[str]) -> str:
    fact_html = "".join(f"<span>{_esc(fact)}</span>" for fact in facts)
    return (
        '<header class="dossier-hero" id="overview">'
        f'<p class="eyebrow">{_esc(eyebrow)}</p><h1>{_esc(title)}</h1>'
        f'<div class="hero-meta">{fact_html}</div></header>'
    )


def _shell(
    *,
    title: str,
    output_type: str,
    body: str,
    nav_items: list[tuple[str, str]],
    extra_actions: str = "",
) -> str:
    nav = "".join(f'<li><a href="#{_esc(anchor)}">{_esc(label)}</a></li>' for anchor, label in nav_items)
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="color-scheme" content="light">\n'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:\">\n"
        f"<title>{_esc(title)}</title>\n<style>{_CSS}</style>\n</head>\n"
        f'<body data-output-type="{_esc(output_type.replace("_", "-"))}">\n'
        '<a class="skip-link" href="#main-content">Skip to content</a>\n'
        '<div class="dossier-shell"><aside class="dossier-rail">'
        '<div class="rail-mark"><div><div class="rail-kicker">Open Pharma Plugins</div>'
        '<div class="rail-title">Field evidence dossier</div></div></div>'
        f'<nav class="dossier-nav" aria-label="Document sections"><ul>{nav}</ul></nav>'
        '</aside><div class="dossier-workspace"><header class="utility-bar">'
        '<span class="draft-state">Draft for MLR review</span><div class="utility-actions">'
        '<button class="action-button" type="button" data-action="sources" aria-pressed="false">'
        "Expand sources</button>"
        f"{extra_actions}"
        '<button class="action-button" type="button" data-action="print">Print / save PDF</button>'
        "</div></header>"
        f'<main class="dossier-main" id="main-content">{body}</main>'
        f"</div></div><script>{_JS}</script></body></html>"
    )


def render_learning_package(data: dict) -> str:
    title = data.get("title", "Learning Package")
    modules = data.get("modules", [])
    source_documents = data.get("source_documents", [])
    parts = [
        _hero(
            title,
            "Approved-source learning package",
            [
                f"{len(modules)} module{'s' if len(modules) != 1 else ''}",
                f"{len(source_documents)} approved source{'s' if len(source_documents) != 1 else ''}",
                f"Generated {data.get('generated_at', '')}",
            ],
        )
    ]
    categories = sorted(
        {
            str(message.get("category", "other")).lower()
            for module in modules
            for message in module.get("key_messages", [])
        }
    )
    for index, module in enumerate(modules, 1):
        parts.append(f'<section class="section-block" data-filter-scope="module" id="module-{index}">')
        parts.append(
            '<div class="section-heading">'
            f'<span class="section-index">{index:02d}</span><h2>{_esc(module.get("title", ""))}</h2></div>'
        )
        parts.append('<div class="module-meta">')
        if module.get("product"):
            parts.append(f"<span><strong>Product</strong> {_esc(module['product'])}</span>")
        if module.get("therapeutic_area"):
            parts.append(f"<span><strong>Therapeutic area</strong> {_esc(module['therapeutic_area'])}</span>")
        parts.append("</div>")

        objectives = module.get("objectives", [])
        if objectives:
            parts.append("<h3>Learning objectives</h3><ul>")
            for objective in objectives:
                parts.append(
                    f"<li>{_esc(objective.get('objective', ''))} "
                    f'<span class="badge badge-blue">{_esc(objective.get("bloom_level", ""))}</span></li>'
                )
            parts.append("</ul>")

        messages = module.get("key_messages", [])
        if messages:
            parts.append('<h3>Key messages</h3><div class="filter-bar" aria-label="Filter key messages">')
            parts.append(
                '<input class="filter-search" type="search" data-action="search" '
                'aria-label="Search key messages" placeholder="Search messages">'
            )
            parts.append(
                '<button class="filter-button" type="button" data-action="filter" '
                'data-filter="all" aria-pressed="true">All</button>'
            )
            for category in categories:
                parts.append(
                    '<button class="filter-button" type="button" data-action="filter" '
                    f'data-filter="{_esc(category)}" aria-pressed="false">{_esc(category)}</button>'
                )
            parts.append('</div><div class="card-grid">')
            for message in messages:
                category = str(message.get("category", "other")).lower()
                parts.append(f'<article class="card" data-category="{_esc(category)}">')
                parts.append(f'<span class="badge {_badge_class(category)}">{_esc(category)}</span>')
                parts.append(f"<p>{_esc(message.get('message', ''))}</p>")
                parts.append(_sources_html(message.get("sources", [])))
                parts.append("</article>")
            parts.append("</div>")

        talking_points = module.get("talking_points", [])
        if talking_points:
            parts.append('<h3>Talking points</h3><div class="card-grid">')
            for point in talking_points:
                parts.append('<article class="card">')
                parts.append(f'<div class="card-header">{_esc(point.get("situation", ""))}</div>')
                parts.append(f"<p><strong>Approved response</strong><br>{_esc(point.get('approved_response', ''))}</p>")
                if point.get("supporting_data"):
                    parts.append(f"<p><strong>Supporting data</strong><br>{_esc(point['supporting_data'])}</p>")
                parts.append(_sources_html(point.get("sources", [])))
                parts.append("</article>")
            parts.append("</div>")

        objections = module.get("common_objections", [])
        if objections:
            parts.append('<h3>Objection handling</h3><div class="card-grid">')
            for objection in objections:
                parts.append('<article class="card">')
                parts.append(f'<div class="card-header">{_esc(objection.get("objection", ""))}</div>')
                parts.append(
                    f"<p><strong>Approved response</strong><br>{_esc(objection.get('approved_response', ''))}</p>"
                )
                parts.append(_sources_html(objection.get("sources", [])))
                parts.append("</article>")
            parts.append("</div>")
        parts.append("</section>")

    if source_documents:
        parts.append('<section class="section-block" id="sources"><h2>Source ledger</h2><ul>')
        parts.extend(f"<li>{_esc(document)}</li>" for document in source_documents)
        parts.append("</ul></section>")
    parts.append(f'<div class="meta">Generated: {_esc(data.get("generated_at", ""))}</div>')

    nav_items = [("overview", "Overview")]
    nav_items.extend(
        (f"module-{index}", module.get("title", f"Module {index}")) for index, module in enumerate(modules, 1)
    )
    if source_documents:
        nav_items.append(("sources", "Source ledger"))
    return _shell(
        title=title,
        output_type="learning_package",
        body="".join(parts),
        nav_items=nav_items,
    )


def render_roleplay_kit(data: dict) -> str:
    title = data.get("title", "Role-Play Kit")
    parts = [
        _hero(
            title,
            "Facilitated practice dossier",
            [data.get("topic", ""), data.get("hcp_persona", ""), f"Generated {data.get('generated_at', '')}"],
        ),
        '<section class="section-block" id="scenario"><h2>Practice scenario</h2>',
        f"<p>{_esc(data.get('scenario', ''))}</p><p><strong>HCP persona</strong><br>"
        f"{_esc(data.get('hcp_persona', ''))}</p></section>",
    ]
    objectives = data.get("objectives", [])
    if objectives:
        parts.append('<section class="section-block" id="objectives"><h2>Session objectives</h2><ul>')
        for objective in objectives:
            parts.append(f"<li>{_esc(objective.get('objective', ''))}</li>")
        parts.append("</ul></section>")
    messages = data.get("key_messages", [])
    if messages:
        parts.append('<section class="section-block" id="messages"><h2>Approved message bank</h2>')
        parts.append('<div class="card-grid">')
        for message in messages:
            category = str(message.get("category", "other")).lower()
            parts.append(f'<article class="card" data-category="{_esc(category)}">')
            parts.append(f'<span class="badge {_badge_class(category)}">{_esc(category)}</span>')
            parts.append(f"<p>{_esc(message.get('message', ''))}</p>")
            parts.append(_sources_html(message.get("sources", [])))
            parts.append("</article>")
        parts.append("</div></section>")
    objections = data.get("common_objections", [])
    if objections:
        parts.append('<section class="section-block" id="objections"><h2>Objection practice</h2>')
        parts.append('<div class="card-grid">')
        for objection in objections:
            parts.append('<article class="card">')
            parts.append(f'<div class="card-header">{_esc(objection.get("objection", ""))}</div>')
            parts.append(
                '<div class="facilitator-note" data-facilitator hidden><strong>Approved response</strong><br>'
                f"{_esc(objection.get('approved_response', ''))}"
                f"{_sources_html(objection.get('sources', []))}</div>"
            )
            parts.append("</article>")
        parts.append("</div></section>")
    prompts = data.get("facilitator_prompts", [])
    if prompts:
        parts.append('<section class="section-block" id="facilitation"><h2>Facilitator run of show</h2>')
        for prompt in prompts:
            parts.append('<article class="card">')
            parts.append(f'<span class="badge badge-blue">{_esc(prompt.get("stage", ""))}</span>')
            parts.append(f"<p>{_esc(prompt.get('prompt', ''))}</p>")
            parts.append(
                f'<div class="facilitator-note" data-facilitator hidden>{_esc(prompt.get("coaching_intent", ""))}</div>'
            )
            parts.append("</article>")
        parts.append("</section>")
    rubric = data.get("evaluation_rubric", [])
    if rubric:
        parts.append('<section class="section-block" id="rubric"><h2>Evaluation rubric</h2>')
        for criterion in rubric:
            parts.append('<article class="card">')
            parts.append(
                f'<div class="card-header">{_esc(criterion.get("criterion", ""))} '
                f'<span class="rubric-weight">{_esc(criterion.get("weight_pct", 0))}%</span></div><ul>'
            )
            parts.extend(f"<li>{_esc(item)}</li>" for item in criterion.get("evidence_to_observe", []))
            parts.append("</ul></article>")
        parts.append("</section>")
    parts.append(f'<div class="meta">Generated: {_esc(data.get("generated_at", ""))}</div>')
    facilitator_action = (
        '<button class="action-button" type="button" data-action="facilitator-mode" '
        'aria-pressed="false">Facilitator mode</button>'
    )
    return _shell(
        title=title,
        output_type="roleplay_kit",
        body="".join(parts),
        nav_items=[
            ("overview", "Overview"),
            ("scenario", "Scenario"),
            ("objectives", "Objectives"),
            ("messages", "Message bank"),
            ("objections", "Objections"),
            ("facilitation", "Run of show"),
            ("rubric", "Rubric"),
        ],
        extra_actions=facilitator_action,
    )


def render_assessment(data: dict) -> str:
    title = data.get("title", "Assessment")
    questions = data.get("mcq_questions", [])
    scenarios = data.get("scenario_questions", [])
    parts = [
        _hero(
            title,
            "Knowledge and application check",
            [
                f"{len(questions)} multiple-choice question{'s' if len(questions) != 1 else ''}",
                f"{len(scenarios)} scenario{'s' if len(scenarios) != 1 else ''}",
                f"Passing score {int(data.get('passing_score_pct', 0.8) * 100)}%",
            ],
        )
    ]
    if questions:
        parts.append('<section class="section-block" id="questions"><h2>Knowledge check</h2>')
        for index, question in enumerate(questions, 1):
            question_id = str(question.get("question_id") or f"question-{index}")
            safe_id = "".join(
                character if character.isalnum() or character in "-_" else "-" for character in question_id
            )
            answer_id = f"answer-{safe_id}"
            difficulty = str(question.get("difficulty", ""))
            parts.append('<article class="card">')
            parts.append(
                f'<div class="card-header">Question {index} '
                f'<span class="badge {_badge_class(difficulty)}">{_esc(difficulty)}</span></div>'
                f'<p>{_esc(question.get("question", ""))}</p><ol type="A">'
            )
            for option in question.get("options", []):
                parts.append(f"<li>{_esc(option.get('text', ''))}</li>")
            parts.append("</ol>")
            parts.append(
                '<button class="action-button" type="button" data-action="toggle-answer" '
                f'aria-controls="{_esc(answer_id)}" aria-expanded="false">Reveal answer</button>'
            )
            parts.append(f'<div id="{_esc(answer_id)}" class="answer-panel" hidden>')
            parts.append(f"<p><strong>Correct answer: {_esc(question.get('correct_answer', ''))}</strong></p>")
            parts.append(f"<p>{_esc(question.get('explanation', ''))}</p>")
            if question.get("source"):
                parts.append(_source_html(question["source"]))
            parts.append("</div></article>")
        parts.append("</section>")
    if scenarios:
        parts.append('<section class="section-block" id="scenarios"><h2>Scenario practice</h2>')
        for index, scenario in enumerate(scenarios, 1):
            answer_id = f"scenario-answer-{index}"
            difficulty = str(scenario.get("difficulty", ""))
            parts.append('<article class="card">')
            parts.append(
                f'<div class="card-header">Scenario {index} '
                f'<span class="badge {_badge_class(difficulty)}">{_esc(difficulty)}</span></div>'
                f"<p>{_esc(scenario.get('scenario', ''))}</p>"
                f"<p><strong>Persona</strong><br>{_esc(scenario.get('hcp_persona', ''))}</p>"
            )
            parts.append(
                '<button class="action-button" type="button" data-action="toggle-answer" '
                f'aria-controls="{answer_id}" aria-expanded="false">Reveal answer</button>'
            )
            parts.append(f'<div id="{answer_id}" class="answer-panel" hidden><ul>')
            parts.extend(f"<li>{_esc(point)}</li>" for point in scenario.get("ideal_response_points", []))
            parts.append(f"</ul>{_sources_html(scenario.get('sources', []))}</div></article>")
        parts.append("</section>")
    source_documents = data.get("source_documents", [])
    if source_documents:
        parts.append('<section class="section-block" id="sources"><h2>Source ledger</h2><ul>')
        parts.extend(f"<li>{_esc(document)}</li>" for document in source_documents)
        parts.append("</ul></section>")
    parts.append(f'<div class="meta">Generated: {_esc(data.get("generated_at", ""))}</div>')
    nav_items = [("overview", "Overview")]
    if questions:
        nav_items.append(("questions", "Knowledge check"))
    if scenarios:
        nav_items.append(("scenarios", "Scenarios"))
    if source_documents:
        nav_items.append(("sources", "Source ledger"))
    return _shell(
        title=title,
        output_type="assessment",
        body="".join(parts),
        nav_items=nav_items,
    )


def render_roleplay_scorecard(data: dict) -> str:
    topic = data.get("topic", "Role-Play")
    score = float(data.get("score", 0.0))
    score_pct = int(score * 100)
    score_label = "Ready" if score >= 0.8 else "Developing" if score >= 0.6 else "Needs coaching"
    parts = [
        _hero(
            f"Role-play scorecard: {topic}",
            "Observed conversation review",
            [f"Score {score_pct}%", score_label, data.get("hcp_persona", "")],
        )
    ]
    turns = data.get("turns", [])
    if turns:
        parts.append('<section class="section-block" id="transcript"><h2>Conversation transcript</h2>')
        for turn in turns:
            speaker = str(turn.get("speaker", "rep")).lower()
            speaker_label = "HCP" if speaker == "hcp" else "Representative"
            bubble_class = "bubble-hcp" if speaker == "hcp" else "bubble-rep"
            parts.append(
                '<div class="bubble-row">'
                f'<div class="bubble {bubble_class}"><div class="bubble-label">{speaker_label}</div>'
                f"{_esc(turn.get('message', ''))}</div></div>"
            )
        parts.append("</section>")
    claims = data.get("claims_evaluated", [])
    if claims:
        parts.append('<section class="section-block" id="claims"><h2>Claims evaluation</h2>')
        parts.append('<div class="card-grid">')
        for claim in claims:
            status = str(claim.get("status", ""))
            parts.append('<article class="card">')
            parts.append(f'<span class="badge {_badge_class(status)}">{_esc(status)}</span>')
            parts.append(f"<p><strong>{_esc(claim.get('claim', ''))}</strong></p>")
            parts.append(f"<p>{_esc(claim.get('feedback', ''))}</p>")
            if claim.get("source"):
                parts.append(_source_html(claim["source"]))
            parts.append("</article>")
        parts.append("</div></section>")
    strengths = data.get("strengths", [])
    improvements = data.get("areas_for_improvement", [])
    if strengths or improvements:
        parts.append('<section class="section-block" id="coaching"><h2>Coaching debrief</h2>')
        parts.append('<div class="card-grid">')
        if strengths:
            parts.append('<article class="card"><div class="card-header">Strengths</div><ul>')
            parts.extend(f"<li>{_esc(item)}</li>" for item in strengths)
            parts.append("</ul></article>")
        if improvements:
            parts.append('<article class="card"><div class="card-header">Focus next</div><ul>')
            parts.extend(f"<li>{_esc(item)}</li>" for item in improvements)
            parts.append("</ul></article>")
        parts.append("</div></section>")
    source_documents = data.get("source_documents", [])
    if source_documents:
        parts.append('<section class="section-block" id="sources"><h2>Source ledger</h2><ul>')
        parts.extend(f"<li>{_esc(document)}</li>" for document in source_documents)
        parts.append("</ul></section>")
    nav_items = [("overview", "Overview")]
    if turns:
        nav_items.append(("transcript", "Transcript"))
    if claims:
        nav_items.append(("claims", "Claims"))
    if strengths or improvements:
        nav_items.append(("coaching", "Coaching"))
    if source_documents:
        nav_items.append(("sources", "Source ledger"))
    return _shell(
        title=f"Role-play scorecard: {topic}",
        output_type="roleplay_scorecard",
        body="".join(parts),
        nav_items=nav_items,
    )


RENDERERS = {
    "learning_package": render_learning_package,
    "assessment": render_assessment,
    "roleplay_kit": render_roleplay_kit,
    "roleplay_scorecard": render_roleplay_scorecard,
}
