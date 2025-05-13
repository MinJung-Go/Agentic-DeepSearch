from fastmcp import FastMCP
from deepsearch import WebSearchTool, DeepResearchParams

# Create MCP server
mcp = FastMCP("DeepSearch Tools")

# Initialize the search tool once
search_tool = WebSearchTool()

@mcp.tool(name="web_deep_search",description="Perform a comprehensive web research on the given query.",timeout=60)
async def web_deep_search(query: str) -> dict:
    """Perform a comprehensive web research on the given query.
    
    Args:
        query: The research question or topic to investigate
        
    Returns:
        Dictionary containing research results organized by sub-questions
    """
    response = await WebSearchTool().__call__(DeepResearchParams(searchQuery=query))

    return response

@mcp.resource("content://{url}")
async def get_web_content(url: str) -> str:
    """Get the text content from a web URL.
    
    Args:
        url: The web URL to extract content from
        
    Returns:
        Extracted text content
    """
    return search_tool.extract_url_content(url)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=8080)
    # mcp.run(transport="stdio") 
