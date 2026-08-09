# `src/loadgenerator/locustfile.py`

> Documentación generada automáticamente. Última actualización basada en el commit indexado más reciente.

## Propósito
Este archivo (`locustfile.py`) es un generador de carga de locust.io que simula comportamientos de usuarios en una aplicación web. Fue diseñado para probar y medir el rendimiento y estabilidad del sitio.

## Componentes principales
* `index`: Realiza una solicitud GET a la raíz del sitio.
* `setCurrency`: Selecciona un código de moneda aleatorio y realiza una solicitud POST a `/setCurrency` con ese valor.
* `browseProduct`: Busca un producto aleatorio en la lista de productos y realiza una solicitud GET a su página de detalles.
* `viewCart`: Realiza una solicitud GET a la página del carrito de compras.
* `addToCart`: Agrega un producto aleatorio al carrito de compras y realiza una solicitud POST a `/cart` con los detalles del producto.
* `empty_cart`: Vacía el carrito de compras realizando una solicitud POST a `/cart/empty`.
* `checkout`: Completa el proceso de pago simulando la entrada de información de tarjeta de crédito y realiza una solicitud POST a `/cart/checkout`.
* `logout`: Realiza una solicitud GET para logout.
* `UserBehavior` (clase): Define un conjunto de tareas que simulan comportamientos de usuarios, como cambiar de moneda, buscar productos, agregar al carrito, etc.
* `WebsiteUser` (clase): Extiende la clase `FastHttpUser` y utiliza el conjunto de tareas definido en `UserBehavior`.

## Dependencias
Este archivo depende de:

* locust: biblioteca para generación de carga y testing de aplicaciones web.
* faker: biblioteca para generar datos falsos (Fake Data).
* datetime: módulo estándar de Python para trabajar con fechas y horas.

## Riesgos de deuda técnica
No se han encontrado riesgos de deuda técnica evidentes en este código.