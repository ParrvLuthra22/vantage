# vantage-sdk

Instrumentation for LLM agent applications.

## Install

```bash
pip install vantage-sdk
```

## Usage

```python
import vantage

vantage.init(api_key="...", base_url="https://api.vantage.dev", project="my-agent")


@vantage.trace()
def my_agent_call(user_input: str):
    with vantage.span("llm_call") as sp:
        # ... your logic ...
        sp.set_llm(model="gpt-4o-mini", input_tokens=120, output_tokens=80, cost_usd=0.0002)
```

Full documentation lands in Week 6.
