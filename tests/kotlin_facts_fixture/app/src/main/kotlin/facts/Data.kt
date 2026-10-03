package facts

import jakarta.persistence.Entity
import jakarta.persistence.Table
import org.jetbrains.exposed.sql.Table as ExposedTable
import org.jetbrains.exposed.dao.id.IntIdTable
import org.jetbrains.exposed.sql.insert
import org.jetbrains.exposed.sql.selectAll
import org.springframework.data.jpa.repository.JpaRepository
import org.springframework.data.repository.CrudRepository

@Entity
@Table(name = "owners")
class Owner(var id: Int = 0, var name: String = "")

@Entity
class PetType(var id: Int = 0)

interface OwnerRepository : JpaRepository<Owner, Int> {
    fun findByName(name: String): List<Owner>
}

interface PetTypeRepository : CrudRepository<PetType, Int>

object Users : IntIdTable("app_users") {
    val name = varchar("name", 50)
}

object AuditLog : org.jetbrains.exposed.sql.Table() {
    val msg = varchar("msg", 200)
}

class OwnerService(private val owners: OwnerRepository, private val types: PetTypeRepository) {
    fun rename(id: Int, name: String) {
        val o = owners.findById(id)
        owners.save(Owner(id, name))
    }

    fun typeCount(): Long = types.count()

    fun users(): Int {
        Users.insert { it[name] = "x" }
        AuditLog.insert { it[msg] = "listed" }
        return Users.selectAll().count().toInt()
    }
}
