package demo

@RestController
@RequestMapping("/api/books")
class BookController(private val repo: BookRepo) {
    @GetMapping("/{id}")
    fun one(@PathVariable id: Long) = repo.load(id)

    @PostMapping
    @PreAuthorize("hasRole('ADMIN')")
    fun create(@RequestBody b: String) = repo.save(b)

    @RequestMapping(value = ["/search"], method = [RequestMethod.GET, RequestMethod.POST])
    fun search() = repo.load(0)
}

class BookRepo {
    fun load(id: Long): String = ""
    fun save(b: String): String = b
}

class Jobs {
    @Scheduled(fixedRate = 60000)
    fun cleanup() {}

    @KafkaListener(topics = ["orders"])
    fun onOrder(msg: String) {}
}
