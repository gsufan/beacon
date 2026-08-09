# `src/cartservice/src/cartstore/ICartStore.cs`

> Documentación generada automáticamente. Última actualización basada en el commit indexado más reciente.

## Propósito
El módulo `ICartStore` define una interfaz para manejar operaciones de carrito de compras en el servicio de cartservice. Establece las funciones básicas para agregar, eliminar y obtener información sobre los artículos en un carrito.

## Componentes principales
* `AddItemAsync`: Agrega un artículo al carrito concreto.
* `EmptyCartAsync`: Elimina todos los artículos del carrito especificado.
* `GetCartAsync`: Devuelve el contenido del carrito especifico.
* `Ping`: Verifica la conectividad del servicio de cartservice (no tiene efectos en el estado del carrito).

## Dependencias
Este archivo depende de `System.Threading.Tasks` y hace referencia a la clase abstracta `Hipstershop.Cart`.