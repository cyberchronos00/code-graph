"""GraphQL root fields as protocol endpoints (#34): cg_code_graph/graphql.py over tests/graphql_fixture (SDL files, an
Apollo resolver map, Apollo Client operations and a codegen hook, a Nest resolver, graphene / strawberry / ariadne
schemas and Python gql clients)."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cg_code_graph.graphql import Doc, camel, first_level, root_fields  # noqa: E402
from cg_code_graph.indexer import index_project  # noqa: E402
from cg_code_graph.core.store import GraphStore  # noqa: E402

FIX = ROOT / "tests" / "graphql_fixture"
Q, M, S = "endpoint:graphql:Query.", "endpoint:graphql:Mutation.", "endpoint:graphql:Subscription."


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    db = tmp_path_factory.mktemp("gql") / "g.db"
    st = index_project(FIX, db, "gql")
    return st, sqlite3.connect(db), db


def _edges(con, kind, ep):
    col, other = ("dst", "src") if kind in ("SENDS_TO", "TEST_CALLS") else ("src", "dst")
    return {r[0]: (r[1], json.loads(r[2] or "{}")) for r in con.execute(
        f"select {other}, confidence, attrs from edges where kind=? and {col}=?", (kind, ep))}


def _node(con, nid):
    r = con.execute("select kind, attrs, entry_kind from nodes where id=?", (nid,)).fetchone()
    return (r[0], json.loads(r[1]), r[2]) if r else (None, None, None)


def test_document_parser():
    d = Doc("""
      query Q($id: ID!) @live { a: user(id: $id) { ...U ... on Admin { level } friends(first: 2) { id } } __typename }
      fragment U on User { name email __typename }
      mutation { save(input: {x: "}"}) { ok } }
      type Query { me: User! list(first: Int = 1): [User!]! }
      extend type Query { extra: Int }
      schema { query: Query mutation: Rw }
    """)
    assert [(o["name"], o["kind"]) for o in d.ops] == [("Q", "query"), (None, "mutation")]
    rf = root_fields(d.ops[0]["sel"])
    assert [f[0] for f in rf] == ["user", "__typename"]
    assert first_level(rf[0][1], d.frags) == ["name", "email", "level", "friends"]
    assert [f[0] for f in root_fields(d.ops[1]["sel"])] == ["save"]
    assert set(d.types["Query"]) == {"me", "list", "extra"} and d.types["Query"]["list"]["type"] == "[User!]!"
    assert d.schema == {"query": "Query", "mutation": "Rw"}
    assert camel("product_create") == "productCreate" and camel("top_rated_movies", "strawberry") == "topRatedMovies"


def test_schema_fields(graph):
    st, con, _ = graph
    g = st["graphql"]
    assert g["graphql_files"] == 2 and g["schema_fields"] == 8
    kind, a, _e = _node(con, Q + "books")
    assert kind == "endpoint" and a["protocol"] == "graphql" and a["root"] == "Query" and a["field"] == "books"
    assert a["type"] == "[Book!]!" and a["declared_in"] == "schema/library.graphql:10" and a["served"] == "schema"
    assert _node(con, Q + "stats")[1]["declared_in"] == "src/server/stats.graphql:2"     # extend type Query
    assert _node(con, "endpoint:graphql:Book.author") == (None, None, None)              # object types: part 2
    assert _node(con, Q + "stockLevel")[1]["declared_in"] == "py/inventory.py:5"          # ariadne gql("""...""")


def test_js_resolver_map(graph):
    _, con, _ = graph
    assert set(_edges(con, "RECEIVED_BY", Q + "books")) == {"function:src/server/resolvers.ts#bookQueries.books"}  # spread
    assert set(_edges(con, "RECEIVED_BY", Q + "author")) == {"function:src/server/resolvers.ts#resolvers.Query.author"}
    assert set(_edges(con, "RECEIVED_BY", Q + "stats")) == {"function:src/server/resolvers.ts#statsResolver"}   # reference
    assert set(_edges(con, "RECEIVED_BY", M + "addBook")) == {"function:src/server/resolvers.ts#resolvers.Mutation.addBook"}
    sub = _edges(con, "RECEIVED_BY", S + "bookAdded")
    assert len(sub) == 1 and next(iter(sub.values()))[1]["how"] == "resolver map (subscribe)"
    # declared without a resolver: served by the default resolver, no RECEIVED_BY
    assert not _edges(con, "RECEIVED_BY", Q + "ping")


def test_operations(graph):
    _, con, _ = graph
    books = _edges(con, "SENDS_TO", Q + "books")
    assert set(books) == {"function:src/web/Books.tsx#Books"}         # not refetchQueries / readQuery
    a = books["function:src/web/Books.tsx#Books"][1]
    assert (a["operation"], a["operation_kind"], a["library"]) == ("GetBooks", "query", "apollo")
    assert a["fields"] == ["id", "title", "author"]                      # ...BookParts expanded, __typename dropped
    assert set(_edges(con, "SENDS_TO", M + "addBook")) == {"function:src/web/Books.tsx#AddBook"}   # alias created:
    assert set(_edges(con, "SENDS_TO", S + "bookAdded")) == {"function:src/web/Books.tsx#LiveBooks"}  # inline gql
    assert _node(con, Q + "isLoggedIn") == (None, None, None)          # @client field
    # the codegen hook's callers send; the generated hook is a wrapper
    assert set(_edges(con, "SENDS_TO", Q + "author")) == {"function:src/web/AuthorPage.tsx#AuthorPage"}
    # a commented-out operation is not one
    assert not _edges(con, "SENDS_TO", Q + "ping")


def test_python_resolvers(graph):
    _, con, _ = graph
    assert set(_edges(con, "RECEIVED_BY", Q + "allItems")) == {"method:py.catalog.ItemQueries.resolve_all_items"}
    create = _edges(con, "RECEIVED_BY", M + "createItem")
    assert set(create) == {"method:py.catalog.CreateItem.perform_mutation"} and create[
        "method:py.catalog.CreateItem.perform_mutation"][0] == "resolved"
    delete = _edges(con, "RECEIVED_BY", M + "deleteItem")      # inherited BaseMutation.mutate: heuristic
    assert set(delete) == {"method:py.catalog.BaseMutation.mutate"} and delete["method:py.catalog.BaseMutation.mutate"][0] == "heuristic"
    assert delete["method:py.catalog.BaseMutation.mutate"][1]["inherited_from"] == "BaseMutation"
    _k, cnt, _e = _node(con, Q + "itemCount")                  # no resolve_item_count: the default resolver
    assert cnt["served"] == "graphene" and not _edges(con, "RECEIVED_BY", Q + "itemCount")
    assert _node(con, "endpoint:graphql:Query.id") == (None, None, None)   # Item is no root type
    # strawberry: @strawberry.field(name=..), strawberry.field(resolver=fn), @strawberry.mutation
    assert set(_edges(con, "RECEIVED_BY", Q + "topReview")) == {"method:py.reviews.ReviewQuery.best_review"}
    assert set(_edges(con, "RECEIVED_BY", Q + "recentReviews")) == {"function:py.reviews.load_reviews"}
    assert set(_edges(con, "RECEIVED_BY", M + "postReview")) == {"method:py.reviews.ReviewMutation.post_review"}
    # strawberry_django.field() and plain annotations: served by the default resolver
    assert _node(con, Q + "reviewerList")[1]["served"] == "strawberry" and _node(con, Q + "reviewerCount")[1]["served"] == "strawberry"
    # ariadne: @query.field("..") and mutation.set_field("..", fn)
    assert set(_edges(con, "RECEIVED_BY", Q + "stockLevel")) == {"function:py.inventory.resolve_stock_level"}
    assert set(_edges(con, "RECEIVED_BY", M + "restock")) == {"function:py.inventory.resolve_restock"}


def test_python_clients(graph):
    st, con, _ = graph
    assert set(_edges(con, "SENDS_TO", Q + "stockLevel")) == {"function:py.client.current_stock"}
    tc = _edges(con, "TEST_CALLS", M + "createItem")             # QUERY constants posted by a test
    assert set(tc) == {"function:py.tests.catalog_checks.test_create_item"}
    assert tc["function:py.tests.catalog_checks.test_create_item"][1]["orig"] == "SENDS_TO"
    # `query = """..."""` assigned in two tests: each test posts its own; a `query` parameter is none of them
    assert set(_edges(con, "TEST_CALLS", Q + "allItems")) == {"function:py.tests.catalog_checks.test_local_query_a"}
    assert set(_edges(con, "TEST_CALLS", Q + "itemCount")) == {"function:py.tests.catalog_checks.test_local_query_b"}
    # the f-string's `{field}` root is computed: not sent
    assert st["graphql"].get("dynamic_root_fields") is None


def test_nest_twin_and_view(graph):
    _, con, db = graph
    recv = _edges(con, "RECEIVED_BY", Q + "shelves")
    assert set(recv) == {"method:src/nest/shelf.resolver.ts#ShelfResolver.shelves"}
    assert _node(con, Q + "shelves")[2] is None              # the route stays the entry point
    from cg_code_graph.protocols.view import collect
    ep = collect(GraphStore(db))
    assert Q + "shelves" not in ep                          # shown as the route
    r = ep["route:GRAPHQL Query.shelves"]
    assert r["protocol"] == "graphql" and r["linked"] and [s["fn"] for s in r["senders"]] == ["function:src/web/shelves.ts#fetchShelves"]
    assert "unguarded" in r["checks"]
    assert ep[Q + "retired"]["checks"] == ["no_receiver"]   # no schema declares it
    assert ep[Q + "books"]["linked"] and not ep[Q + "books"]["checks"]
    assert ep[Q + "ping"]["side"] == "receive" and not ep[Q + "ping"]["checks"]      # declared, unsent: no no_sender
    assert ep[M + "createItem"]["checks"] == ["test_sender_only"]
