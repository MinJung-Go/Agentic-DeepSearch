import os
import re
import ast
import json
import httpx
from abc import ABC
from typing import Any
from pydantic import BaseModel, Field
from concurrent.futures import ThreadPoolExecutor

from LLM import OpenAIClient
from prompts import EXPERT_PLANNING_SYSTEM, EXPERT_KEYWORD_SYSTEM, EXPERT_JUDEGE_SYSTEM, SUMMARY_SYSTEM

class DeepResearchParams(BaseModel):
    searchQuery: str = Field(..., description="The question of the research")

class WebSearchTool(ABC):
    name = "websearch"
    description = "Searching on the internet"
    param_anno: BaseModel = DeepResearchParams
    llm = OpenAIClient(base_url=os.environ.get("BASE_URL","https://api.openai.com/v1"), api_key=os.environ.get("OPEN_AI_KEY"))

    def web_search_bing(self, query: str, page_num: int = 3):
        """
        Perform web search using Bing Search API.

        Args:
            query (str): Search keywords.
            page_num (int): Number of result pages, defaults to 3.

        Returns:
            tuple: A tuple containing two lists - first list is search result URLs, second list is search result titles.

        Raises:
            Exception: Catches and prints any exceptions that occur.

        """
        subscription_key = os.environ.get("Bing_API_KEY")
        endpoint = 'https://api.bing.microsoft.com/v7.0/search'
        search_urls = []
        titles = []
        mkt = 'en-US'
        params = {'q': query, 'mkt': mkt, 'count': page_num*10}
        headers = {'Ocp-Apim-Subscription-Key': subscription_key}
        try:
            response = httpx.get(endpoint, headers=headers, params=params)
            response.raise_for_status()
            web_content = response.json()
            search_urls = [single_page["url"]
                            for single_page in web_content["webPages"]["value"]]
            titles = [single_page["name"]
                        for single_page in web_content["webPages"]["value"]]
        except Exception as e:
            print(e)
        return search_urls, titles

    def extract_url_content(self, url: str):
        """
        Extract plain text content from a specified URL.

        Args:
            url (str): The URL to extract content from.

        Returns:
            str: Extracted plain text content, returns empty string if extraction fails.

        """
        try:
            from bs4 import BeautifulSoup
            import requests
            r = requests.get(url)
            soup = BeautifulSoup(r.text, 'html.parser')
            text = soup.get_text().strip()
            return text
        except Exception as e:
            print(e)
            return ""

    async def reranker_by_gpt(self, user_question, search_title):
        """
        Re-rank search titles using GPT model.

        Args:
            user_question (str): User's question.
            search_title (list): List of search titles.

        Returns:
            list: Re-ranked list of search title indices.

        """
        temp_messages = [
            {
                "role": "system",
                "content": "You are a search engine. I give you a set of webpage titles and URLs. You need to determine if these titles are relevant to my query. If relevant, return the indices in JSON format, example: {\"releative_titles\":[0,1,2]} \n\nReference title list: {search_title} \n\nUser question: {user_question}".replace("{search_title}", str(search_title)).replace("{user_question}",str(user_question))
                }
        ]
        temp_response = await self.llm(temp_messages, model="gpt-4.1", temperature=0.7)
        # print(temp_response)
        # Quickly parse the "releative_titles" sequence list from temp_response
        try:
            try:
                temp_response= json.loads(str(temp_response.choices[0].message.content))["releative_titles"]
            except:
                # Find JSON string using regex
                import re
                temp_response = json.loads(re.findall(r'\{.*?\}', str(temp_response))[0])["releative_titles"]
            return temp_response
        except Exception as e:
            # Print error line
            import sys
            print(f"Error: {e}\nLine: {sys.exc_info()[-1].tb_lineno}")

            # Return list [0,...n] with length matching search_titles
            return [i for i in range(len(search_title))]

    async def search_by_bing(self, searchKeyWords: str, searchQuestion: str, page_num: int = 1)-> dict[str, Any] | None:
        """
        Perform search using Bing and return results.

        Args:
            searchKeyWords (str): Keywords to search for.
            searchQuestion (str): User's question for relevance filtering.
            page_num (int, optional): Number of pages to search, defaults to 1.

        Returns:
            dict[str, Any] | None: Dictionary containing search results and related info, returns None if no results found.

        """
        search_urls, search_title = self.web_search_bing(
            searchKeyWords, page_num)
        
        if len(search_urls) == 0:
            return {}
        
        # Add relevance filtering using gpt-4o-mini to check if titles match user's query in kwargs["input"], keep only relevant URLs
        user_question = searchQuestion
        temp_response = await self.reranker_by_gpt(user_question, search_title)
        # temp_response = await self.reranker_by_embedding(user_question, search_title)
        search_urls = [search_urls[idx] for idx in temp_response]
        search_title = [search_title[idx] for idx in temp_response]
        
        with ThreadPoolExecutor(max_workers=len(search_urls)/int(page_num)) as executor:
            res = list(executor.map(self.extract_url_content, search_urls))

        respone_content = {}
        for idx in range(len((res))):
            if len(res[idx]) >=50 and len(res[idx]) < 10000:
                respone_content[f"Reference {idx}"] = search_title[idx] + "\n" + str(res[idx])

        # return respone_content
        return respone_content

    async def __call__(self, params: DeepResearchParams) -> dict[str, Any] | None:
        """
        Async call function for handling deep research queries.

        Args:
            params (DeepResearchParams): Class instance containing query parameters.

        Returns:
            dict[str, Any] | None: Query result dictionary, or None for no results. Returns error dictionary if exceptions occur.

        The function first performs initial planning by calling GPT-4.1 model to get potential keywords.
        Then it enters an iterative process where each potential keyword is processed - performing Bing search
        and generating summaries. During iteration, GPT-4.1 model is called to judge if current results
        sufficiently cover the original question. If not, planning continues and iteration repeats.
        Maximum iteration count prevents infinite loops. Returns error dictionary if exceptions occur.
        """
        searchQuery = params.searchQuery
  
        # Initial planning phase
        potential_keyword = await self.llm([
            {"role":"system","content": EXPERT_PLANNING_SYSTEM},
            {"role":"user","content": searchQuery}
            ], model="gpt-4.1")
        print(potential_keyword)
        potential_keyword = ast.literal_eval(re.findall(r'\[\s*{.*?}\s*\]', str(potential_keyword.choices[0].message.content),re.DOTALL)[0])
        result = {} # Dictionary to store final results
        max_iterations = 3  # Max iterations to prevent infinite loops
        iteration = 0  # Iteration count
        IF_END = False # Flag to determine if finished
        
        while iteration < max_iterations and IF_END == False:
            try:
                # Process search results
                res_index = 0
                for item in potential_keyword:
                    if "<|RETHINK AND PLANNING>|" not in item['sub_question']:
                        # continue
                        # Combine sub-question with search results to extract keywords from item['sub_question']
                        temp_keywords = await self.llm([
                            {"role":"system","content": EXPERT_KEYWORD_SYSTEM.replace("{ref_content}",str(result)).replace("{question}",item['sub_question'])},
                            {"role":"user","content": item['sub_question']}
                            ], model="gpt-4.1")
                        # 从temp_keyword.choices[0].message.content正则出列表
                        print(temp_keywords.choices[0].message.content)
                        try:
                            temp_keywords = list(re.findall(r'\["(.*?)"\]', str(temp_keywords.choices[0].message.content),re.DOTALL))
                        except:
                            import sys
                            print(temp_keywords)
                            print(sys.exc_info()[-1].tb_lineno)
                            temp_keywords = [temp_keywords]
                        for temp_keyword in temp_keywords:
                            temp_search_result = await self.search_by_bing(temp_keyword, "参考用户问题：["+str(item['sub_question'])+"]\n\n请结合搜索关键词:[{temp_keyword}]\n总结搜索到的网页的内容".replace("{temp_keyword}",temp_keyword))
                            
                        temp_summary = await self.llm([
                            {"role":"system","content": SUMMARY_SYSTEM.replace("{ref_content}", str(temp_search_result))
                                                    .replace("{question}", item['sub_question'])},
                            {"role":"user","content": item['sub_question']}
                            ], model="gpt-4.1")
                        result[item['sub_question']] = temp_summary.choices[0].message.content
                        res_index += 1
                    
                    else:
                        potential_keyword = await self.llm([
                        {"role":"system","content": EXPERT_PLANNING_SYSTEM},
                        {"role":"user","content": searchQuery+"\n\nTODO List completed but task not finished, please continue planning: " + str(result)}
                        ], model="gpt-4.1")
                        potential_keyword = ast.literal_eval(re.findall(r'\[\s*{.*?}\s*\]', str(potential_keyword.choices[0].message.content),re.DOTALL)[0])
                        break
                    
                # Call LLM to judge if result covers original question
                temp_if_end = await self.llm([
                    {"role":"system","content": EXPERT_JUDEGE_SYSTEM.replace("{ref_content}", str(result)).replace("{question}", str(params.searchQuery))}], model="gpt-4.1")
                IF_END = True if "True" in temp_if_end.choices[0].message.content else False
                if IF_END == True:
                    pass
                else:
                    potential_keyword = await self.llm([
                        {"role":"system","content": EXPERT_PLANNING_SYSTEM},
                        {"role":"user","content": searchQuery+"\n\nTODO List completed but task not finished, please continue planning: " + str(result)}
                        ], model="gpt-4.1")
                    potential_keyword = ast.literal_eval(re.findall(r'\[\s*{.*?}\s*\]', str(potential_keyword.choices[0].message.content),re.DOTALL)[0])

                
            except Exception as e:
                # Print error line
                import sys
                print(f"Error: {e}\nLine: {sys.exc_info()[-1].tb_lineno}")
                return {"error": f"{e}"}
            
            iteration += 1
            return result
        
if __name__ == "__main__":
    import asyncio
    response = asyncio.run(WebSearchTool().__call__(DeepResearchParams(searchQuery="llm加速推理引擎有哪些")))
    print(response)
