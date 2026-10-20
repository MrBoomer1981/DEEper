"""A fake OpenAI-compatible LLM for free end-to-end runs: the whole app works, nothing is paid.

    uv run dev/mock_llm.py                          # http://127.0.0.1:8765/v1
    uv run dev/mock_llm.py --port 8766 --log calls.jsonl

Point a run at it with environment variables, which beat config.json and the writer toggle:

    FAST_LLM=openai:mock SMART_LLM=openai:mock STRATEGIC_LLM=openai:mock \
    OPENAI_BASE_URL=http://127.0.0.1:8765/v1 OPENAI_API_KEY=mock uv run app.py

It answers by phrases of the prompts it gets (gpt-researcher's and ours: sub-queries, source rating,
subtopics, draft titles, sections, fact-check, follow-ups, clarification, questions) with answers of
the shape the caller parses. Anything else gets a short report section citing two URLs from the prompt,
with a figure the fact-check «corrects» (12,5 -> 11,9) and one it cannot find, so a run also exercises the
check's fixes. If a prompt's wording changes, its answer falls back to that section: a run still ends,
but the step that parses it (say, the subtopic list) gets nothing useful. Report-writing calls stream
(SSE), and /embeddings answers too, since gpt-researcher retries 10 times with backoff when they fail.
--log writes one JSON line per chat call: which step it was, the model and the token cap it was sent.
"""

import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STEPS = {  # --log: which step a call was, by a phrase of its prompt
    "titles": "draft section title headers",
    "subtopics": "Construct a list of subtopics",
    "section": "construct a detailed report on the subtopic",
    "introduction": "Prepare a detailed report introduction",
    "conclusion": "write a concise conclusion",
}


def answer(prompt: str) -> str:
    if "Предложи 5 тем для новых исследований" in prompt:  # worker/suggest.py: follow-ups
        return ('[{"kind": "deeper", "query": "Себестоимость натрий-ионных ячеек при массовом производстве в Китае", '
                '"why": "нет разбивки затрат", "mode": "quick"}]')
    if "уточняющих вопросов" in prompt:  # suggest.questions()
        return '[{"q": "Какой регион важнее?", "options": ["Россия", "Китай", "Весь мир"]}]'
    if "Перепиши тему исследования" in prompt:  # suggest.refine()
        return "«Перспективы натрий-ионных аккумуляторов в России»"
    if "Сократи его до краткой версии" in prompt:  # worker/shorten.py: two links of the report, nothing new
        urls = [u for u in dict.fromkeys(re.findall(r"https?://[^\s)\]'\"<>]+", prompt))][:2] or ["https://www.iea.org"]
        return ("# Краткая версия\n\n## Главное\n\nКороткий вывод из отчёта со ссылкой на источник "
                + "".join(f"([{i}]({u}))" for i, u in enumerate(urls, 1)) + ". Свои выводы — без ссылок.\n")
    if "=== ОТЧЁТ ===" in prompt:  # worker/ask.py
        return "В отчёте нет данных о ценах в России.\n\n**ИССЛЕДОВАТЬ:** Цены на натрий-ионные аккумуляторы в России в 2026 году"
    if "Translate each numbered statement" in prompt:  # worker/factcheck.py
        count = int(re.search(r"JSON array of (\d+) strings", prompt).group(1))
        return json.dumps([f"English statement {i}" for i in range(1, count + 1)])
    if "You check statements from a research report" in prompt:  # the fact-check's judge
        items = re.findall(r"^(\d+)\. (?:Before: .*\n\s+Statement: )?(.*)$", prompt.split("Statements:")[-1], re.M)
        return "\n".join(f"{n}. MISMATCH | Страница называет 11,9 млрд, а не 12,5." if "12,5" in text
                         else f"{n}. NOT_FOUND | Страница об этом молчит." if "150 ГВт" in text else f"{n}. SUPPORTED"
                         for n, text in items)
    if "You check one citation from a research report" in prompt:  # eval's citation judge
        return "SUPPORTED"
    if "Ты редактор исследовательского отчёта" in prompt:  # the fact-check's correction
        sentence = prompt.split("Предложение:\n", 1)[1].split("\n\nВыдержки", 1)[0]
        return sentence.replace("12,5", "11,9")
    if "researchGoal" in prompt:  # deep mode: queries
        return '[{"query": "sodium-ion battery market outlook 2026", "researchGoal": "market"}]'
    if "learnings" in prompt or "followUpQuestions" in prompt:  # deep mode: what a query taught
        return '{"learnings": ["Na-ion дешевле LFP"], "followUpQuestions": ["Кто производители?"], "citations": {}}'
    if "agent_role_prompt" in prompt:  # gpt-researcher picks an agent
        return '{"server": "🔬 Research Agent", "agent_role_prompt": "You are a research assistant."}'
    if "list of strings in the following format" in prompt:  # sub-queries
        return '["sodium-ion battery market outlook", "sodium-ion battery manufacturers capacity 2030"]'
    if STEPS["subtopics"] in prompt:  # detailed mode
        return '{"subtopics": [{"task": "Технология натрий-ионных аккумуляторов"}, {"task": "Рынок и производители"}]}'
    if STEPS["titles"] in prompt:
        return "### Устройство\n### Сравнение с литием"
    if "Return ONLY a JSON array" in prompt:  # search/sources.py: every candidate rated 3
        count = int(re.search(r"JSON array of (\d+) integers", prompt).group(1)) if "integers" in prompt else 1
        return json.dumps([3] * count)
    urls = [u for u in dict.fromkeys(re.findall(r"https?://[^\s)\]'\"<>]+", prompt)) if "iea.org" not in u][:2]
    if len(urls) == 2:
        return ("## Раздел\n\nРынок натрий-ионных аккумуляторов в 2025 году вырос до 12,5 млрд долларов по оценке аналитиков "
                f"([Источник A]({urls[0]})). Производители планируют выпуск 150 ГВт·ч к 2030 году, что подтверждают "
                f"отраслевые обзоры ([Источник B]({urls[1]})).\n\n## Источники\n\n- [A]({urls[0]})\n- [B]({urls[1]})\n")
    return "## Раздел\n\nТекст отчёта по теме со ссылкой на [IEA](https://www.iea.org).\n"


class Handler(BaseHTTPRequestHandler):
    log_path = None

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.endswith("/embeddings"):
            inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
            data = [{"object": "embedding", "index": i, "embedding": [((hash(str(x)) >> k) % 100) / 100 for k in range(16)]}
                    for i, x in enumerate(inputs)]
            return self._json({"object": "list", "data": data, "model": "mock", "usage": {"prompt_tokens": 1, "total_tokens": 1}})
        prompt = "\n".join(str(m.get("content")) for m in body["messages"])
        text = answer(prompt)
        if self.log_path:
            step = next((name for name, phrase in STEPS.items() if phrase in prompt), None)
            cap = body.get("max_tokens") or body.get("max_completion_tokens")
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": round(time.time(), 2), "step": step, "model": body.get("model"), "max_tokens": cap},
                                   ensure_ascii=False) + "\n")
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in (({"role": "assistant", "content": text}, None), ({}, "stop")):
                chunk = {"id": "mock", "object": "chat.completion.chunk", "created": 0, "model": body.get("model"),
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json({"id": "mock", "object": "chat.completion", "created": 0, "model": body.get("model"),
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100}})

    def _json(self, data: dict) -> None:
        out = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log", help="append one JSON line per chat call to this file")
    args = parser.parse_args()
    Handler.log_path = args.log
    print(f"Mock LLM: http://127.0.0.1:{args.port}/v1", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
