// Basic Kotlin test file for extractor validation
// Tests: classes, interfaces, functions, methods, properties

package com.example.demo

// Top-level function
fun topLevelFunction(name: String): String {
    return "Hello, $name"
}

// Simple class
class User(val name: String, val age: Int) {

    // Property with custom getter
    val isAdult: Boolean
        get() = age >= 18

    // Method
    fun greet(): String {
        return "Hello, I'm $name"
    }

    // Private method
    private fun internalHelper(): Int {
        return age * 2
    }

    // Nested class
    class Address(val street: String, val city: String) {
        fun fullAddress(): String = "$street, $city"
    }
}

// Interface
interface Repository {
    fun findById(id: Int): User?
    fun save(user: User): Boolean
}

// Data class
data class Person(
    val firstName: String,
    val lastName: String,
    val email: String
) {
    fun fullName(): String = "$firstName $lastName"
}

// Object declaration (singleton)
object DatabaseConfig {
    val host: String = "localhost"
    val port: Int = 5432

    fun connectionString(): String = "jdbc:postgresql://$host:$port"
}

// Companion object
class Service {
    companion object {
        val DEFAULT_TIMEOUT: Int = 30

        fun create(): Service = Service()
    }

    fun execute(): String = "executing"
}

// Enum class
enum class Status {
    PENDING,
    ACTIVE,
    INACTIVE,
    DELETED
}

// Sealed class
sealed class Result {
    data class Success(val value: String) : Result()
    data class Error(val message: String) : Result()
    object Loading : Result()
}

// Generic class
class Container<T>(val content: T) {
    fun get(): T = content
    fun map<R>(transform: (T) -> R): Container<R> {
        return Container(transform(content))
    }
}

// Extension function
fun String.isEmail(): Boolean {
    return this.contains("@")
}

// Type alias
typealias UserMap = Map<String, User>
