import Vapor

func routes(_ app: Application) throws {
    app.get("health") { req in "ok" }

    let protected = app.grouped("v1").grouped(User.authenticator(), User.guardMiddleware())
    protected.get("orders", ":id") { req async throws -> Order in
        try await OrderService().find(req.parameters.get("id"), on: req.db)
    }

    app.group("admin") { admin in
        admin.post("reindex") { req in
            try await OrderService().reindex()
        }
    }

    try app.register(collection: BooksController())
}

struct BooksController: RouteCollection {
    func boot(routes: RoutesBuilder) throws {
        let books = routes.grouped("books")
        books.get(use: index)
        books.delete(":bookId", use: self.delete)
    }

    func index(req: Request) async throws -> [Book] {
        try await Book.query(on: req.db).all()
    }

    func delete(req: Request) async throws -> HTTPStatus {
        .noContent
    }
}

struct OrderService {
    func find(_ id: String?, on db: Database) async throws -> Order {
        throw Abort(.notFound)
    }

    func reindex() async throws -> HTTPStatus {
        .ok
    }
}
