package facts

import io.ktor.client.HttpClient
import io.ktor.client.request.get
import io.ktor.client.request.request
import io.ktor.http.HttpMethod
import io.ktor.resources.Resource
import io.ktor.server.application.Application
import io.ktor.server.resources.get
import io.ktor.server.resources.post
import io.ktor.server.routing.routing
import retrofit2.Retrofit
import retrofit2.http.GET
import retrofit2.http.POST

interface TopicsApi {
    @GET("topics")
    suspend fun topics(): List<String>
}

interface AuthApi {
    @POST("login")
    suspend fun login(): String
}

object Network {
    val topics: TopicsApi = Retrofit.Builder().baseUrl(BuildConfig.API_URL).build().create(TopicsApi::class.java)
    val auth: AuthApi = Retrofit.Builder().baseUrl("https://auth.example.com/").build().create(AuthApi::class.java)
}

class DogApi(private val client: HttpClient) {
    suspend fun breeds() = client.get { url("https://dog.example.com/api/breeds/list/all") }
    suspend fun vote() = client.request {
        method = HttpMethod.Post
        url("https://dog.example.com/api/votes")
    }
}

@Resource("/articles")
class Articles(val sort: String? = "new") {
    @Resource("new")
    class New(val parent: Articles = Articles())

    @Resource("{id}")
    class Id(val parent: Articles = Articles(), val id: Long)
}

fun Application.resources() {
    routing {
        get<Articles> { call.respondText("list") }
        get<Articles.Id> { article -> call.respondText("one") }
        post<Articles.New> { call.respondText("new") }
    }
}

// URLs built by project helpers (#99)
const val CAT_BASE = "https://cat.example.com"

class CatApi(private val client: HttpClient) {
    suspend fun facts() = client.get { cats("api/facts") }
    suspend fun breed() = client.get(catUrl("breeds/1"))

    private fun HttpRequestBuilder.cats(path: String) {
        url {
            takeFrom("https://cat.example.com/")
            encodedPath = path
        }
    }
}

fun catUrl(path: String): String = "$CAT_BASE/v1/$path"
