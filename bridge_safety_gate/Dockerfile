FROM golang:1.21-alpine AS builder
WORKDIR /app
COPY go.mod ./
COPY *.go ./
RUN go build -o bridge_safety_gate .

FROM alpine:3.19
WORKDIR /app
COPY --from=builder /app/bridge_safety_gate .
EXPOSE 8091
ENV BRIDGE_URL=http://127.0.0.1:8090
CMD ["./bridge_safety_gate"]
