# `src/frontend/handlers.go`

> Documentación generada automáticamente. Última actualización basada en el commit indexado más reciente.

**Frontend Server Documentation**

### Overview

The Frontend Server is a Go application responsible for handling HTTP requests and responding with the necessary information to facilitate the shopping experience on our platform.

### Assistant Handler Method

```go
func (fe *frontendServer) assistantHandler(w http.ResponseWriter, r *http.Request) {
    // ...
}
```

**Purpose:** The `assistantHandler` method is responsible for rendering the assistant chatbot template. It retrieves the currencies and renders the template with common data injected.

### Logout Handler Method

```go
func (fe *frontendServer) logoutHandler(w http.ResponseWriter, r *http.Request) {
    // ...
}
```

**Purpose:** The `logoutHandler` method is responsible for logging out the user by setting cookies to expire and updating the location header.

### Get Product By ID Method

```go
func (fe *frontendServer) getProductByID(w http.ResponseWriter, r *http.Request) {
    // ...
}
```

**Purpose:** The `getProductByID` method is responsible for retrieving a product by its ID and returning it as JSON data.

### Chat Bot Handler Method

```go
func (fe *frontendServer) chatBotHandler(w http.ResponseWriter, r *http.Request) {
    // ...
}
```

**Purpose:** The `chatBotHandler` method is responsible for handling requests to the shopping assistant chatbot. It sends a POST request to the LLM service, retrieves the response, and returns it as JSON data.

### Set Currency Handler Method

```go
func (fe *frontendServer) setCurrencyHandler(w http.ResponseWriter, r *http.Request) {
    // ...
}
```

**Purpose:** The `setCurrencyHandler` method is responsible for setting the user's currency preference. It validates the request payload, sets a cookie to store the selected currency, and updates the location header.

### Choose Ad Method

```go
func (fe *frontendServer) chooseAd(ctx context.Context, ctxKeys []string, log logrus.FieldLogger) *pb.Ad {
    // ...
}
```

**Purpose:** The `chooseAd` method is responsible for querying for advertisements available and randomly choosing one. It ignores any errors retrieving the ad since it is not critical.

### Render HTTP Error Method

```go
func renderHTTPError(log logrus.FieldLogger, r *http.Request, w http.ResponseWriter, err error, code int) {
    // ...
}
```

**Purpose:** The `renderHTTPError` method is responsible for rendering an error template with common data injected. It sets the status code and writes the response.

### Inject Common Template Data Method

```go
func injectCommonTemplateData(r *http.Request, payload map[string]interface{}) map[string]interface{} {
    // ...
}
```

**Purpose:** The `injectCommonTemplateData` method is responsible for injecting common data into a template. It retrieves session ID, current currency, and other platform-specific information.

### Current Currency Method

```go
func currentCurrency(r *http.Request) string {
    // ...
}
```

**Purpose:** The `currentCurrency` method is responsible for retrieving the user's current currency preference from cookies.

### Session ID Method

```go
func sessionID(r *http.Request) string {
    // ...
}
```

**Purpose:** The `sessionID` method is responsible for retrieving the session ID from the request context. If no session ID is found, it returns an empty string.

This documentation provides an overview of the Frontend Server's methods and their purposes, helping developers understand how to use and maintain this application.