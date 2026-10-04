import strawberry
import strawberry_django


def load_reviews() -> list[str]:
    return []


@strawberry.type
class ReviewQuery:
    recent_reviews: list[str] = strawberry.field(resolver=load_reviews)

    @strawberry.field(name="topReview")
    def best_review(self) -> str:
        return ""


@strawberry.type
class ReviewMutation:
    @strawberry.mutation
    def post_review(self, text: str) -> bool:
        return True


review_schema = strawberry.Schema(query=ReviewQuery, mutation=ReviewMutation)


@strawberry.type(name="Query")
class ReviewerQuery:
    reviewer_count: int
    reviewer_list: list[str] = strawberry_django.field()


merged_schema = strawberry.Schema(query=ReviewerQuery)
