"""A fake OpenAI-compatible endpoint for testing the LLM path offline.

It behaves like a lazy but well-formed model: keeps the first two candidates
of each episode (tightening nothing), occasionally returns a bad beat id or a
fenced/prose-wrapped JSON (to exercise the validators), and for the show-level
ranking returns the ids in reverse order.

    python tests/mock_llm_server.py 8765
    python -m promofinder run --input <scripts> --out /tmp/x --provider openai \
        --base-url http://127.0.0.1:8765/v1 --api-key test
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

CALLS = {"n": 0}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "generateContent" in self.path:                      # Gemini shape
            user = body["contents"][-1]["parts"][0]["text"]
        else:                                                   # OpenAI / Anthropic shape
            m = body["messages"][-1]["content"]
            user = m if isinstance(m, str) else m[0]["text"]
        CALLS["n"] += 1
        if '"order"' in user:
            ids = re.findall(r'"id": "(P\d+)"', user)
            content = json.dumps({"order": [{"id": i, "reason": f"mock reason for {i}"} for i in reversed(ids)]})
        else:
            cands = re.findall(r"^(C\d+) \((\w+), (\d+)s\): segments \[(.*)\]$", user, re.M)
            promos = []
            for k, (cid, kind, dur, segs) in enumerate(cands[:3]):
                seg_list = [json.loads(x) for x in re.findall(r"\{[^}]*\}", segs)]
                if k == 2:
                    seg_list = [{"from": "E999.1", "to": "E999.2"}]          # invalid id -> must fall back
                promos.append({"candidate": cid, "keep": k != 1 or CALLS["n"] % 2 == 0, "segments": seg_list,
                               "hook": f"mock hook {cid}", "cliffhanger": f"mock cliff {cid}",
                               "why": f"mock why {cid}", "scores": {"hook": 4, "cliffhanger": 4, "standalone": 3},
                               "overall": 9 - k, "risks": []})
            content = json.dumps({"promos": promos})
            if CALLS["n"] % 3 == 0:
                content = "Sure! Here is the JSON:\n```json\n" + content + "\n```"   # prose-wrapped
        if "generateContent" in self.path:
            out = {"candidates": [{"content": {"parts": [{"text": content}]}}],
                   "usageMetadata": {"promptTokenCount": len(user) // 4, "candidatesTokenCount": len(content) // 4}}
        elif self.path.endswith("/messages"):
            out = {"content": [{"type": "text", "text": content}],
                   "usage": {"input_tokens": len(user) // 4, "output_tokens": len(content) // 4}}
        else:
            out = {"choices": [{"message": {"content": content}}],
                   "usage": {"prompt_tokens": len(user) // 4, "completion_tokens": len(content) // 4}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8765), H).serve_forever()
