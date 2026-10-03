func build() -> Registry {
    let registry = Registry()
    registry.add(Circle(r: 1.0))
    registry.add(side: 2.0)
    return registry
}

let report = Report(store: Store())
print(report.render(build()))
print(build().total())
