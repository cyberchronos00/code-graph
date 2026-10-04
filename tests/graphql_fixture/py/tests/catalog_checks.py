CREATE_ITEM = """
    mutation CreateItem($name: String) {
        createItem(name: $name) {
            ok
        }
    }
"""


def _field_query(field):
    return f"""
        query {{
            {field} {{
                id
            }}
        }}
    """


def test_create_item(api_client):
    response = api_client.post_graphql(CREATE_ITEM, {"name": "x"})
    assert response


def test_dynamic(api_client):
    api_client.post_graphql(_field_query("allItems"))


def test_local_query_a(api_client):
    query = """
        query { allItems { id } }
    """
    api_client.post_graphql(query)


def test_local_query_b(api_client):
    query = """
        query { itemCount }
    """
    api_client.post_graphql(query)


def test_query_parameter(api_client, query):
    api_client.post_graphql(query)
