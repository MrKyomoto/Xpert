import sys
from dotenv import load_dotenv
from code.agent.core import Agent

# Load environment variables
load_dotenv()

def main():
    print("Initializing PBL Lesson Polish Agent MVP...")
    
    # Check for API key
    import os
    if not os.getenv("API_KEY") and not os.getenv("OPENAI_API_KEY"):
        print("Warning: API_KEY not set in environment. LLM calls may fail.")
        print("Please create a .env file with API_KEY=your_key")
    
    # Initialize a teaching expert agent
    system_prompt = """
    你是一位资深的教学设计专家，精通项目式学习(PBL)的设计与评价。
    你的任务是协助用户审阅和打磨教案。
    你可以使用提供的工具来读取教案文件、写入修改建议，并记录讨论过程。
    
    在审阅教案时，请关注：
    1. 驱动性问题的设计是否合理
    2. 任务链的拆解是否清晰
    3. 思维显性化
    4. 预设与学习支架
    """
    
    expert = Agent(name="教学设计专家", role_prompt=system_prompt)
    
    print("\nAgent ready! Type 'exit' or 'quit' to stop.")
    print("-" * 50)
    
    while True:
        try:
            user_input = input("\nYou: ")
            if user_input.lower() in ['exit', 'quit']:
                break
                
            if not user_input.strip():
                continue
                
            print(f"\n[教学设计专家] is thinking...")
            response = expert.chat(user_input)
            print(f"\n教学设计专家:\n{response}")
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\nError: {str(e)}")

if __name__ == "__main__":
    main()
