// Kotlin inheritance test file
// Tests: extends, implements, abstract classes, interfaces

// Simple inheritance
open class Animal(val name: String) {
    open fun speak(): String = "..."
}

class Dog(name: String) : Animal(name) {
    override fun speak(): String = "Woof!"
}

class Cat(name: String) : Animal(name) {
    override fun speak(): String = "Meow!"
}

// Interface implementation
interface Flyable {
    fun fly(): String
}

interface Swimmable {
    fun swim(): String
}

// Multiple interface implementation
class Duck(name: String) : Animal(name), Flyable, Swimmable {
    override fun speak(): String = "Quack!"
    override fun fly(): String = "Flying south"
    override fun swim(): String = "Swimming in pond"
}

// Abstract class
abstract class Shape {
    abstract fun area(): Double
    abstract fun perimeter(): Double

    fun describe(): String = "Shape with area ${area()}"
}

// Concrete implementation of abstract class
class Rectangle(val width: Double, val height: Double) : Shape() {
    override fun area(): Double = width * height
    override fun perimeter(): Double = 2 * (width + height)
}

class Circle(val radius: Double) : Shape() {
    override fun area(): Double = Math.PI * radius * radius
    override fun perimeter(): Double = 2 * Math.PI * radius
}

// Generic class inheritance
class Box<T>(val content: T)

class NumberBox(content: Number) : Box<Number>(content)

// Sealed class hierarchy
sealed class TreeNode {
    data class Leaf(val value: Int) : TreeNode()
    data class Branch(val left: TreeNode, val right: TreeNode) : TreeNode()
    object Empty : TreeNode()
}

// Nested inheritance
class Outer {
    open inner class Inner {
        open fun message(): String = "Inner"
    }

    class DeepInner : Inner() {
        override fun message(): String = "DeepInner"
    }
}

// Delegation pattern
interface Printer {
    fun print(message: String)
}

class ConsolePrinter : Printer {
    override fun print(message: String) {
        println(message)
    }
}

class LoggingPrinter(private val delegate: Printer) : Printer by delegate {
    fun log(message: String) {
        println("LOG: $message")
        delegate.print(message)
    }
}
