from fieldshop.cart import Cart


def test_owner():
    c = Cart("a")
    assert c.owner == "a"
