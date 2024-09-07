import asyncio
import logging
from typing import Any, Dict
import websockets
from websockets.server import WebSocketServerProtocol
from cache_manager import MultiLevelCache

# Include the MultiLevelCache and related imports (assumed to be in the same module or imported correctly)
# from your_module import MultiLevelCache, CachePriority

class NotificationService:
    """
    NotificationService class to handle in-app notifications using WebSocket.
    This class manages connections and sends real-time notifications to users.
    """

    def __init__(self, cache_manager: MultiLevelCache):
        self.logger = self._initialize_logger()
        self.cache_manager = cache_manager
        self.active_connections: Dict[int, WebSocketServerProtocol] = {}  # {user_id: websocket_connection}

    def _initialize_logger(self) -> logging.Logger:
        """
        Initialize logger for the NotificationService.

        Returns:
            logging.Logger: Configured logger instance.
        """
        logger = logging.getLogger("NotificationService")
        logger.setLevel(logging.DEBUG)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)

        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(formatter)

        logger.addHandler(console_handler)
        return logger

    async def register_connection(self, user_id: int, connection: WebSocketServerProtocol):
        """
        Register a WebSocket connection for a user.

        Args:
            user_id (int): The ID of the user.
            connection (WebSocketServerProtocol): The WebSocket connection object.
        """
        if not user_id or not connection:
            self.logger.error("Invalid user_id or connection. Registration failed.")
            return

        self.active_connections[user_id] = connection
        # Cache the connection in memory cache for quick access
        self.cache_manager.put(f"user_connection_{user_id}", connection, CachePriority.HIGH)
        self.logger.info(f"Registered WebSocket connection for user {user_id}.")

    async def unregister_connection(self, user_id: int):
        """
        Unregister a WebSocket connection for a user.

        Args:
            user_id (int): The ID of the user.
        """
        if user_id in self.active_connections:
            await self.active_connections[user_id].close()
            del self.active_connections[user_id]
            # Invalidate the cache entry
            self.cache_manager.invalidate(f"user_connection_{user_id}")
            self.logger.info(f"Unregistered WebSocket connection for user {user_id}.")
        else:
            self.logger.warning(f"Tried to unregister non-existent connection for user {user_id}.")

    async def send_in_app_notification(self, user_id: int, notification_data: Dict[str, Any]) -> bool:
        """
        Send an in-app notification to the user via WebSocket.

        Args:
            user_id (int): The ID of the user to send the notification to.
            notification_data (Dict[str, Any]): The data of the notification to be sent.

        Returns:
            bool: True if the notification was successfully sent, False otherwise.
        """
        connection = self.cache_manager.get(f"user_connection_{user_id}")
        if not connection:
            self.logger.warning(f"User {user_id} is not connected. Notification not sent.")
            return False

        try:
            await connection.send(str(notification_data))
            self.logger.info(f"Notification sent to user {user_id}: {notification_data}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to send notification to user {user_id}: {e}")
            return False

    async def broadcast_notification(self, notification_data: Dict[str, Any]):
        """
        Broadcast a notification to all connected users.

        Args:
            notification_data (Dict[str, Any]): The data of the notification to be broadcast.
        """
        if not notification_data:
            self.logger.error("No notification data provided for broadcasting.")
            return

        self.logger.info(f"Broadcasting notification to all connected users: {notification_data}")
        failed_deliveries = []

        for user_id, connection in list(self.active_connections.items()):
            try:
                await connection.send(str(notification_data))
                self.logger.info(f"Broadcasted notification to user {user_id}.")
            except Exception as e:
                self.logger.error(f"Failed to broadcast notification to user {user_id}: {e}")
                failed_deliveries.append(user_id)

        if failed_deliveries:
            self.logger.warning(f"Failed to deliver notifications to users: {failed_deliveries}")

# Example WebSocket server setup
async def handle_connection(websocket: WebSocketServerProtocol, path: str, notification_service: NotificationService):
    """
    Handle incoming WebSocket connections.

    Args:
        websocket (WebSocketServerProtocol): The WebSocket connection object.
        path (str): The path of the WebSocket connection.
        notification_service (NotificationService): The NotificationService instance to manage connections.
    """
    # For demonstration, user_id is hardcoded. In real usage, derive it from authentication tokens or connection params.
    user_id = 1
    await notification_service.register_connection(user_id, websocket)

    try:
        async for message in websocket:
            notification_service.logger.info(f"Received message from user {user_id}: {message}")
    finally:
        await notification_service.unregister_connection(user_id)

# Start the WebSocket server
async def main():
    # Initialize the MultiLevelCache
    cache_manager = MultiLevelCache("notification_service_cache", 1000, 10_000_000, "notification_service_cache.db")

    # Initialize the NotificationService with cache manager
    notification_service = NotificationService(cache_manager)

    server = await websockets.serve(
        lambda ws, path: handle_connection(ws, path, notification_service),
        "localhost",
        6789
    )

    notification_service.logger.info("WebSocket server started on ws://localhost:6789")
    await server.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())