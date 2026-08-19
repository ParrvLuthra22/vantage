"""
Minimal Vesper-like orchestrator instrumented with Vantage.
Demonstrates: multi-span traces, parent-child hierarchy, LLM metadata capture, cost rollup.
"""
import random
import time
from typing import Optional

import vantage
from vantage import span, trace

vantage.init(
    api_key="dev-key-change-me",
    base_url="http://localhost:8000",
    project="vesper",
)


@trace(name="orchestrator.handle")
def handle_user_request(user_input: str, context: Optional[dict] = None) -> str:
    with span("intent_classification") as sp:
        time.sleep(0.05)
        intent = "schedule" if "meeting" in user_input.lower() else "chat"
        sp.set("classified_intent", intent)
        sp.set("input_length", len(user_input))

    with span("agent_selection") as sp:
        time.sleep(0.02)
        agent = "calendar_agent" if intent == "schedule" else "chat_agent"
        sp.set("selected_agent", agent)
        sp.set("routing_confidence", random.uniform(0.7, 0.99))

    with span("agent_execution", attributes={"agent": agent}) as sp:
        time.sleep(0.3)
        input_tokens = random.randint(150, 300)
        output_tokens = random.randint(80, 200)
        cost = (input_tokens * 0.00001) + (output_tokens * 0.00003)
        sp.set_llm(
            model="gemini-2.0-flash",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        result = f"[{agent}] Handled: {user_input}"
        sp.set("output_length", len(result))
        return result


if __name__ == "__main__":
    test_inputs = [
        "book a meeting with priya at 3pm tomorrow",
        "what's the weather like",
        "remind me about the doctor appointment",
        "help me draft an email",
    ]
    for inp in test_inputs:
        result = handle_user_request(inp)
        print(result)
    time.sleep(6)
    print("Done. Check Postgres.")
