import graphene


class Item(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()


class BaseMutation(graphene.Mutation):
    class Arguments:
        name = graphene.String()

    ok = graphene.Boolean()

    @classmethod
    def mutate(cls, root, info, **data):
        return cls.perform_mutation(root, info, **data)


class CreateItem(BaseMutation):
    item = graphene.Field(Item)

    @classmethod
    def perform_mutation(cls, root, info, **data):
        return CreateItem(ok=True)


class DeleteItem(BaseMutation):
    pass


class ItemQueries(graphene.ObjectType):
    all_items = graphene.List(Item)
    item_count = graphene.Int()

    @staticmethod
    def resolve_all_items(root, info):
        return []


class ItemMutations(graphene.ObjectType):
    create_item = CreateItem.Field()
    delete_item = (
        DeleteItem.Field()
    )


class Query(ItemQueries, graphene.ObjectType):
    server_time = graphene.String(resolver=lambda root, info: "now")


class Mutation(ItemMutations, graphene.ObjectType):
    pass


schema = graphene.Schema(query=Query, mutation=Mutation)
