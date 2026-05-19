from app.bot_core import get_application

if __name__ == '__main__':
    # Fetch the configured engine from core
    app = get_application()
    
    # Start the infinite loop
    print("Bot logic initialized. Waiting for connection via POLLING...")
    app.run_polling()