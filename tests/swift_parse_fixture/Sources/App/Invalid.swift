// Not valid Swift: the parse error must be listed with its lines and the declaration it swallowed.
struct Invalid {
    func kept() -> Int { 1 }
}

func broken(_ a: Int -> Int {
    return a
}

func swallowed() -> Int { 2 }

func after() -> Int { 3 }
