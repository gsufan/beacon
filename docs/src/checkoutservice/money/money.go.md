# `src/checkoutservice/money/money.go`

> Documentación generada automáticamente. Última actualización basada en el commit indexado más reciente.

## Propósito
El archivo `money.go` contiene funciones para manipular y verificar valores monetarios en el lenguaje de programación Go. Estas funciones se utilizan para validar, sumar y restar cantidades monetarias, así como para verificar si un valor es cero o positivo.

## Componentes principales

* `IsValid`: Verifica si un valor monetario es válido.
* `signMatches`: Verifica si el signo de un valor monetario coincide con su unidad.
* `validNanos`: Verifica si un valor de nanos es válido.
* `IsZero`: Devuelve true si el valor monetario es cero.
* `IsPositive`: Devuelve true si el valor monetario es positivo y es válido.
* `IsNegative`: Devuelve true si el valor monetario es negativo y es válido.
* `AreSameCurrency`: Verifica si dos valores monetarios tienen la misma moneda.
* `AreEquals`: Compara dos valores monetarios y devuelve true si son iguales.
* `Negate`: Negación de un valor monetario.
* `Must`: Puede generar un error paniqué si el valor proporcionado no es nulo.
* `Sum`: Suma dos valores monetarios. Si uno o ambos valores no son válidos, devuelve un error.
* `MultiplySlow`: Multiplica un valor monetario por un número entero.

## Dependencias
Este archivo depende de la biblioteca `genproto` para manejar la serialización y deserialización de objetos de tipo `pb.Money`.