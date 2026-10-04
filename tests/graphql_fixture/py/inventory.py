from ariadne import MutationType, QueryType, gql, make_executable_schema

type_defs = gql("""
    type Query {
        stockLevel(sku: String!): Int
    }

    type Mutation {
        restock(sku: String!): Boolean
    }
""")

query = QueryType()
mutation = MutationType()


@query.field("stockLevel")
def resolve_stock_level(_, info, sku):
    return 0


def resolve_restock(_, info, sku):
    return True


mutation.set_field("restock", resolve_restock)

inventory_schema = make_executable_schema(type_defs, query, mutation)
