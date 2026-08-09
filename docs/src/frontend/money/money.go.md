# `src/frontend/money/money.go`

> Documentación generada automáticamente. Última actualización basada en el commit indexado más reciente.

## Propósito
El archivo `money.go` contiene funciones para trabajar con valores monetarios representados como objetos `pb.Money`. Estas funciones permiten verificar la validez de los valores, realizar operaciones matemáticas simples y manejar errores.

## Componentes principales
* `IsValid`: Verifica si un valor monetario tiene una firma válida (signos y rangos correctos).
* `signMatches`: Verifica si el signo de dos valores monetarios coincide.
* `validNanos`: Verifica si un valor nanosegundo es válido.
* `IsZero`, `IsPositive`, `IsNegative`: Verifican si un valor monetario es igual a cero, positivo o negativo, respectivamente.
* `AreSameCurrency`: Verifica si dos valores monetarios tienen el mismo código de moneda y no son ambos inexistentes.
* `AreEquals`: Verifica si dos valores monetarios son iguales en cuanto a la moneda y los valores numéricos.
* `Negate`: Inverte el signo de un valor monetario.
* `Must`: Panaic por error si el valor proporcionado no es nulo.
* `Sum`, `MultiplySlow`: Realizan operaciones matemáticas básicas con valores monetarios, como sumar y multiplicar.

## Dependencias
Este archivo depende de la biblioteca `genproto` y utiliza objetos `pb.Money` definidos en ella.