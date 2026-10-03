from shop.agent import run_agent


def test_run_agent_refunds():
    assert run_agent("refund order 1")
