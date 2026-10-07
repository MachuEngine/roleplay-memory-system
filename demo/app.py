"""롤플레이 memory 플레이그라운드 (Gradio).

캐릭터 설정을 입력하고 대화하면서, 말투 유지 · 기억 반영 · 사용자 행동 대리 금지가
실제로 동작하는지 확인하는 화면이다. 대화 처리는 engine.py가 맡는다.

usage:
  DEMO_FAKE_LLM=1 .venv/bin/python demo/app.py   # 키 없이 화면 확인
  .venv/bin/python demo/app.py                   # OPENROUTER_API_KEY 필요
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import gradio as gr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine as E  # noqa: E402
from limits import SESSION_TURN_LIMIT, DailyCounter  # noqa: E402
from llm_client import MissingKey, OpenRouterClient  # noqa: E402

SAMPLE = json.loads((E.DEMO / "sample_character.json").read_text("utf-8"))
MAIN_MODEL = os.environ.get("DEMO_MAIN_MODEL", "google/gemini-2.5-pro")
EXTRACT_MODEL = os.environ.get("DEMO_EXTRACT_MODEL", "google/gemini-2.5-flash-lite")
REPO_URL = "https://github.com/MachuEngine/roleplay-memory-system"
DAILY = DailyCounter()


def make_clients():
    if os.environ.get("DEMO_FAKE_LLM") == "1":
        fake = E.FakeClient()
        return fake, fake, "가짜 응답 모드입니다(DEMO_FAKE_LLM=1). 모델을 호출하지 않습니다."
    try:
        return OpenRouterClient(MAIN_MODEL), OpenRouterClient(EXTRACT_MODEL), None
    except MissingKey:
        return None, None, "API 키가 설정되지 않아 지금은 대화할 수 없습니다."


MAIN_CLIENT, EXTRACT_CLIENT, CLIENT_NOTICE = make_clients()

INTRO = f"""## 롤플레이 memory 플레이그라운드

캐릭터를 설정하고 대화해 보세요. 이 플레이그라운드에서 확인할 수 있는 것은 세 가지입니다.

- **말투 유지**: 입력한 말투와 대화 예시를 대화 내내 지킵니다.
- **기억 반영**: 대화 {E.MEMORY_BATCH}회마다 확정된 사실(L1)과 사건(L2)을 정리해 다음 응답에 넣습니다. 대화창 아래 '기억' 패널에서 볼 수 있습니다.
- **사용자 행동 대리 금지**: 캐릭터는 사용자의 말이나 행동을 대신 쓰지 않도록 지시받습니다. 응답마다 규칙 기반 검사를 돌려, 사용자 행동으로 의심되는 문장을 '응답 검사 기록'에 표시합니다. 형식이 깨진 응답은 표시하지 않고 다시 생성합니다.

메인 모델은 Gemini 2.5 Pro입니다. 대화는 서버에 저장하지 않으며, 새로고침하면 사라집니다.
세션당 {SESSION_TURN_LIMIT}턴까지 대화할 수 있습니다. 설계와 검증 기록: [GitHub]({REPO_URL})
"""


def memory_md(session: E.Session | None) -> str:
    if session is None:
        return "대화를 시작하면 표시됩니다."
    facts, eps = E.memory_view(session)
    pending = len(session.store.pending_turns(session.scope)) // 2
    lines = ["**L1 profile — 확정된 사실**"]
    lines += [f"- {f}" for f in facts] or ["- (아직 없음)"]
    lines += ["", "**L2 episodes — 상태를 바꾼 사건**"]
    lines += [f"- {e}" for e in eps] or ["- (아직 없음)"]
    lines += ["", f"다음 정리까지 대화 {max(0, E.MEMORY_BATCH - pending)}회"]
    failed = [r for r in session.extraction_runs if not r["ok"]]
    if failed:
        lines.append(f"정리 실패 {len(failed)}회 — 원문은 대화 창에 남겨 두고 다음 차례에 다시 시도합니다.")
    return "\n".join(lines)


def log_md(session: E.Session | None) -> str:
    if session is None or not session.records:
        return "아직 기록이 없습니다."
    rows = ["| 턴 | 결과 | 재생성 사유 | 사용자 행동 의심 문장 |", "|---|---|---|---|"]
    clip = lambda s: s.replace("|", "/")[:120] or "-"  # noqa: E731
    for i, r in enumerate(session.records, 1):
        reason = "; ".join(e for errs in r.errors for e in errs)
        warning = " / ".join(r.warnings)
        rows.append(f"| {i} | {r.status} | {clip(reason)} | {clip(warning)} |")
    return "\n".join(rows)


def status_md(session: E.Session | None, note: str = "") -> str:
    parts = [CLIENT_NOTICE or ""]
    if session is not None:
        parts.append(f"남은 턴: 이 세션 {max(0, SESSION_TURN_LIMIT - session.turns_used)}"
                     f" / 오늘 전체 {DAILY.remaining()}")
    if note:
        parts.append(note)
    return "  \n".join(p for p in parts if p)


def load_sample():
    c, p = SAMPLE["character"], SAMPLE["persona"]
    return (c["name"], c["identity"], c["initial_state"], c["speech"], c["examples"],
            p["name"], p["description"])


def start(name, identity, initial_state, speech, examples, user_name, user_desc):
    character = E.Character(name, identity, initial_state, speech, examples)
    persona = E.Persona(user_name, user_desc)
    error = E.setup_error(character, persona)
    if error or MAIN_CLIENT is None:
        return (None, [], memory_md(None), log_md(None), status_md(None, error or ""),
                gr.update(interactive=False), gr.update(), gr.update())
    session = E.new_session(character, persona)
    # 시작하면 설정을 접어 좁은 화면에서도 대화창이 바로 보이게 한다.
    return (session, [], memory_md(session), log_md(session),
            status_md(session, "대화를 시작했습니다. 첫 메시지를 보내 주세요."),
            gr.update(interactive=True), gr.update(open=False), gr.update(open=False))


def send(message, session, history):
    if session is None:
        return history, session, message, log_md(None), status_md(None, "먼저 '대화 시작'을 눌러 주세요.")
    text, record = E.respond(session, message, MAIN_CLIENT, DAILY)
    if record.status in ("차단", "호출 실패"):
        return history, session, message, log_md(session), status_md(session, text)
    history = history + [{"role": "user", "content": message.strip()},
                         {"role": "assistant", "content": text}]
    return history, session, "", log_md(session), status_md(session)


def extract(session):
    if session is not None and E.extraction_due(session):
        E.run_extraction(session, EXTRACT_CLIENT)
    return session, memory_md(session)


with gr.Blocks(title="롤플레이 memory 플레이그라운드") as demo:
    session_state = gr.State(None)
    gr.Markdown(INTRO)
    with gr.Row():
        with gr.Column(scale=2):
            with gr.Accordion("캐릭터 설정", open=True) as char_panel:
                c_name = gr.Textbox(label="이름", max_length=E.NAME_MAX)
                c_identity = gr.Textbox(label="고정 정체성", lines=6,
                                        info="대화 중에 바뀌지 않는 이름·성격·배경·세계 규칙")
                c_state = gr.Textbox(label="초기 상태", lines=3,
                                     info="대화가 시작되는 시점의 관계·장소·상황. 대화가 진행되면 바뀔 수 있습니다.")
                c_speech = gr.Textbox(label="말투", lines=3)
                c_examples = gr.Textbox(label="대화 예시", lines=6,
                                        info="행동은 *별표*, 대사는 \"따옴표\"로 씁니다. 비우면 기본 예시를 씁니다.")
            with gr.Accordion("사용자 페르소나", open=True) as persona_panel:
                u_name = gr.Textbox(label="이름", max_length=E.NAME_MAX)
                u_desc = gr.Textbox(label="설명", lines=4)
            with gr.Row():
                sample_btn = gr.Button("샘플 캐릭터 불러오기")
                start_btn = gr.Button("대화 시작", variant="primary")
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(label="대화", height=560, render_markdown=True)
            msg = gr.Textbox(show_label=False, placeholder="메시지를 입력하세요 (500자 이하)",
                             lines=1, max_lines=4, max_length=E.MESSAGE_MAX,
                             submit_btn="보내기", interactive=False)
            status = gr.Markdown(status_md(None, "설정을 확인하고 '대화 시작'을 눌러 주세요."))
            with gr.Accordion("기억 (L1·L2)", open=True):
                memory_box = gr.Markdown(memory_md(None))
            with gr.Accordion("응답 검사 기록", open=False):
                log_box = gr.Markdown(log_md(None))

    fields = [c_name, c_identity, c_state, c_speech, c_examples, u_name, u_desc]
    demo.load(load_sample, None, fields)
    sample_btn.click(load_sample, None, fields)
    start_btn.click(start, fields, [session_state, chatbot, memory_box, log_box, status, msg,
                                    char_panel, persona_panel])
    msg.submit(send, [msg, session_state, chatbot],
               [chatbot, session_state, msg, log_box, status]
               ).then(extract, [session_state], [session_state, memory_box])


if __name__ == "__main__":
    port = os.environ.get("PORT")              # Render 같은 호스팅이 지정하는 포트
    demo.queue(default_concurrency_limit=4, api_open=False).launch(
        theme=gr.themes.Soft(), server_port=int(port) if port else None)
