from fastapi import WebSocket


class ConnectionManager:

    def __init__(self):
        self.connections: dict[
            str,
            list[tuple[WebSocket, str]],
        ] = {}

    async def connect(
        self,
        live_id: str,
        websocket: WebSocket,
        role: str = "viewer",
    ):

        if live_id not in self.connections:
            self.connections[live_id] = []

        self.connections[
            live_id
        ].append(
            (
                websocket,
                role,
            )
        )

        print(
            "STREETGO WS MANAGER CONNECTED:",
            live_id,
            "ROLE:",
            role,
            "TOTAL:",
            len(
                self.connections[
                    live_id
                ]
            ),
            flush=True,
        )

    def disconnect(
        self,
        live_id: str,
        websocket: WebSocket,
    ):

        connections = (
            self.connections.get(
                live_id
            )
        )

        if not connections:
            return

        self.connections[
            live_id
        ] = [
            item
            for item in connections
            if item[0] is not websocket
        ]

        if not self.connections[
            live_id
        ]:

            self.connections.pop(
                live_id,
                None,
            )

    async def broadcast(
        self,
        live_id: str,
        message: dict,
    ):

        connections = (
            self.connections.get(
                live_id,
                [],
            )
        )

        disconnected = []

        for websocket, role in list(
            connections
        ):

            try:

                await websocket.send_json(
                    message
                )

            except Exception:

                disconnected.append(
                    websocket
                )

        for websocket in disconnected:

            self.disconnect(
                live_id,
                websocket,
            )

    def viewer_count(
        self,
        live_id: str,
    ) -> int:

        connections = (
            self.connections.get(
                live_id,
                [],
            )
        )

        return sum(
            1
            for websocket, role
            in connections
            if role == "viewer"
        )

    def broadcaster_connected(
        self,
        live_id: str,
    ) -> bool:

        connections = (
            self.connections.get(
                live_id,
                [],
            )
        )

        return any(
            role == "broadcaster"
            for websocket, role
            in connections
        )


manager = ConnectionManager()