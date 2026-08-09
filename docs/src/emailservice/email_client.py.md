# `src/emailservice/email_client.py`

> Documentación generada automáticamente. Última actualización basada en el commit indexado más reciente.

## Propósito
El archivo `email_client.py` es el cliente de un servicio de correo electrónico que utiliza gRPC (Protocol Buffers). Su propósito principal es enviar correos electrónicos de confirmación de orden a los clientes.

## Componentes principales
* `send_confirmation_email(email, order)` — Envía un correo electrónico de confirmación de orden con los detalles del email y la orden.

## Dependencias
Este archivo depende de:
* `grpc` para establecer una conexión con el servicio gRPC.
* `demo_pb2` y `demo_pb2_grpc` para definir el esquema de datos utilizados en el servicio gRPC.
* `logger` (importado desde `logger.py`) para registrar información y errores.

## Riesgos de deuda técnica
No se han detectado riesgos evidentes de deuda técnica en este código. Sin embargo, es importante mencionar que la conexión al servicio gRPC utiliza una canalización no segura (`insecure_channel`), lo que puede ser un problema si el tráfico no está cifrado.