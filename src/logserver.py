import socket
import sys
from loguru import logger

LOGS_PORT = 23522


def handle_client(conn: socket.socket) -> None:
    with conn.makefile("r", encoding="utf-16", errors="replace") as client_file:
        for line in client_file:
            clean_line = line.rstrip("\r\n")
            if clean_line:
                logger.info(clean_line)

def start_server() -> None:
    logger.remove()
    logger.add(
        sys.stdout, 
        colorize=True, 
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}"
    )

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    host = "127.0.0.1"

    try:
        server.bind((host, LOGS_PORT))
        server.listen(1)
        logger.warning(f"server started on {host}:{LOGS_PORT}")

        while True:
            logger.info("waiting for connection...")
            try:
                conn, addr = server.accept()
                logger.success(f"connected: {addr[0]}:{addr[1]}")
                
                with conn:
                    handle_client(conn)
                    
                logger.warning("disconnected")
            except (ConnectionResetError, BrokenPipeError):
                logger.warning("connection nroke")
                
    except KeyboardInterrupt:
        logger.info("\nstopped")
    finally:
        server.close()


if __name__ == "__main__":
    start_server()