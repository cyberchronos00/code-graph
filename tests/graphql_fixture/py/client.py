from gql import Client, gql

STOCK_QUERY = gql("""
    query Stock($sku: String!) {
        stockLevel(sku: $sku)
    }
""")


def current_stock(client: Client, sku: str):
    return client.execute(STOCK_QUERY, variable_values={"sku": sku})
