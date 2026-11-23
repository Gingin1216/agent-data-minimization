"""Durable HTTP request budget used by the sequential runner."""
import json
import os

LIMITS = dict(task_requests=32, batch_requests=32, input_tokens=100000, output_tokens=24576, max_output_tokens=1024)


def save(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(temp, path)


class BudgetStop(RuntimeError):
    pass


class Ledger:
    def __init__(self, root, task):
        self.path = root / "budget.json"
        self.task = task
        self.state = json.loads(self.path.read_text())
        self.error = None

    def before(self, request):
        state = self.state
        if state["pending"]:
            raise BudgetStop("unresolved_request")
        if state["tasks"].get(self.task, 0) >= LIMITS["task_requests"]:
            self.error = "task_request_limit"
            raise BudgetStop(self.error)
        if state["requests"] >= LIMITS["batch_requests"]:
            self.error = "batch_request_limit"
            raise BudgetStop(self.error)
        if state["input_tokens"] >= LIMITS["input_tokens"] or state["output_tokens"] >= LIMITS["output_tokens"]:
            self.error = "batch_token_limit"
            raise BudgetStop(self.error)
        body = json.loads(request.content)
        if body["model"] != "qwen3.8-flash" or "tools" not in body:
            raise ValueError("Unexpected model or request")
        if not str(request.url).endswith("/compatible-mode/v1/chat/completions"):
            raise ValueError("Unexpected endpoint")
        assert body["max_tokens"] <= LIMITS["max_output_tokens"]
        state["requests"] += 1
        state["tasks"][self.task] = state["tasks"].get(self.task, 0) + 1
        state["pending"] = True
        state["events"].append(dict(task=self.task, http_status=None, finish_reason=None, usage=None))
        save(self.path, state)

    def after(self, response):
        response.read()
        event = self.state["events"][-1]
        event["http_status"] = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = {}
        event["finish_reason"] = (body.get("choices") or [{}])[0].get("finish_reason")
        usage = body.get("usage")
        event["usage"] = usage
        self.state["pending"] = False
        if response.is_success:
            if not usage or not all(isinstance(usage.get(k), int) and usage[k] >= 0
                                    for k in ("prompt_tokens", "completion_tokens")):
                self.state["pending"] = True
                self.error = "usage_unknown"
            else:
                self.state["input_tokens"] += usage["prompt_tokens"]
                self.state["output_tokens"] += usage["completion_tokens"]
        else:
            code = (body.get("error") or {}).get("code")
            self.error = ("free_quota_exhausted" if code == "AllocationQuota.FreeTierOnly"
                          else "http_429" if response.status_code == 429 else f"http_{response.status_code}")
        save(self.path, self.state)
        if self.error:
            raise BudgetStop(self.error)
