from langchain.agents.react.base import DocstoreExplorer
from langchain.document_loaders import TextLoader, DirectoryLoader
from langchain.docstore.wikipedia import Wikipedia
from langchain.indexes import VectorstoreIndexCreator
from langchain.chat_models import ChatOpenAI
from langchain.llms import OpenAI
from langchain.tools.python.tool import PythonREPLTool
from langchain.utilities import SerpAPIWrapper
from langchain.utilities import GoogleSearchAPIWrapper
from langchain import ElasticVectorSearch
from langchain.vectorstores.elastic_vector_search import ElasticKnnSearch
from langchain.embeddings import ElasticsearchEmbeddings
from elasticsearch import Elasticsearch
from ssl import create_default_context
from langchain.prompts import BaseChatPromptTemplate
from langchain.agents import Tool, AgentExecutor, LLMSingleActionAgent, AgentOutputParser, initialize_agent, Tool
from langchain.schema import AgentAction, AgentFinish, HumanMessage
from langchain import LLMChain
from langchain.chains.summarize import load_summarize_chain
from langchain import PromptTemplate
from langchain.chains import RetrievalQA, RetrievalQAWithSourcesChain, TransformChain
from langchain.memory import ConversationBufferMemory
from langchain.chains.qa_with_sources import load_qa_with_sources_chain, stuff_prompt
import os
from feast import FeatureStore
from langchain.text_splitter import CharacterTextSplitter,  RecursiveCharacterTextSplitter
from langchain.vectorstores.redis import Redis
from langchain.embeddings import OpenAIEmbeddings
import redis
import langchain
from langchain.cache import RedisCache
from langchain.cache import RedisSemanticCache
import json
from typing import List, Union, Any
import re
from langchain.tools import tool
from langchain.agents.agent_toolkits import create_retriever_tool
from langchain.agents.agent_types import AgentType
from langchain.agents.agent_toolkits import create_python_agent
from langchain.vectorstores import FAISS,  DocArrayInMemorySearch
from pydantic import BaseModel, Field
from langchain.agents.agent_toolkits import (
    create_vectorstore_agent,
    VectorStoreToolkit,
    create_vectorstore_router_agent,
    VectorStoreRouterToolkit,
    VectorStoreInfo,
)
from langchain.document_transformers import EmbeddingsRedundantFilter
from langchain.retrievers.document_compressors import DocumentCompressorPipeline
from langchain.text_splitter import CharacterTextSplitter
from langchain.retrievers.document_compressors import EmbeddingsFilter
from langchain.retrievers import ContextualCompressionRetriever
from langchain.docstore.document import Document
from langchain.tools.base import ToolException
from basic_utils import read_txt
from json import JSONDecodeError
from langchain.document_transformers import LongContextReorder
from langchain.retrievers import BM25Retriever, EnsembleRetriever



from dotenv import load_dotenv, find_dotenv
_ = load_dotenv(find_dotenv()) # read local .env file
# You may need to update the path depending on where you stored it
feast_repo_path = "."
redis_password=os.getenv('REDIS_PASSWORD')
redis_url = f"redis://:{redis_password}@localhost:6379"
redis_client = redis.Redis.from_url(redis_url)
# standard cache
# langchain.llm_cache = RedisCache(redis_client)
# semantic cache
# !!!!RedisSentimentCache does not support caching ChatModel outputs.
langchain.llm_cache = RedisSemanticCache(
    embedding=OpenAIEmbeddings(),
    redis_url=redis_url
)


def split_doc(path='./web_data/', path_type='dir', splitter_type = "recursive", chunk_size=200, chunk_overlap=10) -> List[Document]:

    """Splits file or files in directory into different sized chunks with different text splitters.
    
    For the purpose of splitting text and text splitter types, reference: https://python.langchain.com/docs/modules/data_connection/document_transformers/
    
    Keyword Args:

        path (str): file or directory path

        path_type (str): "file" or "dir"

        splitter_type (str): "recursive" or "tiktoken"

        chunk_size (int): smaller chunks in retrieval tend to alleviate going over token limit

        chunk_overlap (int): how many characters or tokens overlaps with the previous chunk

    Returns:

        List[Documents]
    
    """

    if (path_type=="file"):
        loader = TextLoader(path)
    elif (path_type=="dir"):
        loader = DirectoryLoader(path, glob="*.txt", recursive=True)
    documents = loader.load()
    # Option 1: tiktoken from openai
    if (splitter_type=="tiktoken"):
        text_splitter = CharacterTextSplitter.from_tiktoken_encoder(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    # option 2: 
    elif (splitter_type=="recursive"):
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, 
            length_function = len,
            chunk_overlap=chunk_overlap,
            separators=[" ", ",", "\n"])
    docs = text_splitter.split_documents(documents)
    return docs

def split_doc_file_size(txt_path: str, chunk_size=2000) -> List[Document]:

    bytes = os.path.getsize(txt_path)
    # if file is small, don't split
    if bytes<4000:
        docs = [Document(
            page_content = read_txt(txt_path)
        )]
    else:
        docs = split_doc(txt_path, "file", chunk_size=chunk_size)
    return docs


def get_index(path = ".", path_type="file"):

    if (path_type=="file"):
        loader = TextLoader(path, encoding='utf8')
    elif (path_type=="dir"):
        loader = DirectoryLoader(path, glob="*.txt")
    # loader = TextLoader(file, encoding='utf8')
    index = VectorstoreIndexCreator(
        vectorstore_cls=DocArrayInMemorySearch
    ).from_loaders([loader])
    return index

    
def reorder_docs(docs: List[Document]) -> List[Document]:

    """ Reorders documents so that most relevant documents are at the beginning and the end, as in long context, the middle text tend to be ignored.

     See: https://python.langchain.com/docs/modules/data_connection/document_transformers/post_retrieval/long_context_reorder

     Args: 

        docs (List[Document]): a list of Langchain Documents

    Returns:

        a list of reordered Langchain Documents

    """
    reordering = LongContextReorder()
    reordered_docs = reordering.transform_documents(docs)
    return reordered_docs



def create_wiki_tools() -> List[Tool]:

    """
    Creates wikipedia tool used to lookup and search in wikipedia

    Args: None

    Returns: List[Tool]
    
    """
    docstore = DocstoreExplorer(Wikipedia())
    tools = [
        Tool(
            name = "Search",
            func = docstore.search,
            description= "Search for a term in the docstore.",
            handle_tool_error=handle_tool_error,
        ),
        Tool(
            name = "Lookup",
            func = docstore.lookup,
            description = "Lookup a term in the docstore.",
            handle_tool_error=handle_tool_error,
        ),
    ]
    return tools

def create_qa_tools(qa_chain):
    tools = [
        Tool(
            name="QA Document",
            # func = qa_chain.run,
            func = qa_chain.__call__,
            coroutine=qa_chain.acall, #if you want to use async
            description="Useful for answering general questions",
            # return_direct=True,
        ),
    ]
    return tools


def create_search_tools(name: str, top_n: int) -> List[Tool]:

    """
    Creates google search tool

    Args: 

        name (str): type of google search, "google" or "serp"

        top_n (int): how many top results to search

    Returns: List[Tool]

    """

    if (name=="google"):
        search = GoogleSearchAPIWrapper(k=top_n)
        tool = [
            Tool(
            name = "Google Search", 
            description= "useful for when you need to ask with search",
            func=search.run,
            handle_tool_error = handle_tool_error,
        ),
        ]
    elif (name=="serp"):
        search = SerpAPIWrapper() 
        tool = [
            Tool(
            name="SerpSearch",
            description= "useful for when you need to ask with search",
            func=search.run,
            handle_tool_error=handle_tool_error,
        ),
        ]
    return tool

class DocumentInput(BaseModel):
    question: str = Field()

def create_db_tools(retriever: Any, name: str, description: str, llm=OpenAI(), chain_type="stuff") -> List[Tool]:

    """
    Creates databse tool where vector store is used as a retriever. 

    See: https://python.langchain.com/docs/use_cases/question_answering/how_to/vector_db_qa

    Args: 

        retriever (Any): vectorstore retriever

        name (str): tool name

        description (str): tool description

    Key Args:

        llm: OpenAI() by default

        chain_type: "stuff" by default

    Returns: List[Tool]

    """
    tool = [
        Tool(
        args_schema=DocumentInput,
        name=name,
        description=description,
        func=RetrievalQA.from_chain_type(llm=llm, retriever=retriever, chain_type=chain_type),
        handle_tool_error=handle_tool_error,
    ),
    ]
    print(f"Succesfully created database tool: {name}")
    return tool


def create_retriever_tools(vectorstore: Any, tool_name: str, tool_description: str) -> List[Tool]:   

    """Create retriever tools from vector store for conversational retrieval agent
    
    See: https://python.langchain.com/docs/use_cases/question_answering/how_to/conversational_retrieval_agents
    
    Args:

        vectorstore (Any): vector store to be used as retriever

        tool_name: name of the tool

        tool_description: description of the tool's usage

    Returns:

        List[Tool]

    """   

    retriever = vectorstore.as_retriever()
    tool = [create_retriever_tool(
        retriever,
        tool_name,
        tool_description
        )]
    print(f"Succesfully created retriever tool: {tool_name}")

    return tool

def create_vectorstore_agent_toolkit(embeddings, index_name, vs_name, vs_description, llm=OpenAI()):

    """ See: https://python.langchain.com/docs/integrations/toolkits/vectorstore"""

    store = retrieve_faiss_vectorstore(embeddings,index_name)
    vectorstore_info = VectorStoreInfo(
        name=vs_name,
        description=vs_description,
        vectorstore=store,
        )
    router_toolkit = VectorStoreRouterToolkit(
    vectorstores=[vectorstore_info,], llm=llm
        )  
    return router_toolkit

def create_ensemble_retriever(docs: List[Document]) -> Any:

    """See purpose and usage: https://python.langchain.com/docs/modules/data_connection/retrievers/ensemble"""

    bm25_retriever = BM25Retriever.from_documents(docs)
    bm25_retriever.k = 2
    faiss_retriever = FAISS.from_documents(docs, OpenAIEmbeddings()).as_retriever(search_kwargs={"k": 2})
    ensemble_retriever = EnsembleRetriever(retrievers=[bm25_retriever, faiss_retriever], weights=[0.5, 0.5])
    return ensemble_retriever



def create_QASource_chain(chat, vectorstore, docs=None, chain_type="stuff", index_name="redis_web_advice"):

    qa_chain= load_qa_with_sources_chain(chat, chain_type=chain_type, prompt = stuff_prompt.PROMPT, document_prompt= stuff_prompt.EXAMPLE_PROMPT) 
    qa = RetrievalQAWithSourcesChain(combine_documents_chain=qa_chain, retriever=vectorstore.as_retriever(),
                                     reduce_k_below_max_tokens=True, max_tokens_limit=3375,
                                     return_source_documents=True)
    return qa


def create_compression_retriever(vectorstore = "index_web_advice") -> ContextualCompressionRetriever:

    """ Creates a compression retriever given vector store index name. 
    
    TO see its purpose: https://python.langchain.com/docs/modules/data_connection/retrievers/contextual_compression/

    Keyword Args:

        vectorstore: default is "index_web_advice", files contained in "web_data" directory

    """

    embeddings = OpenAIEmbeddings()
    splitter = CharacterTextSplitter(chunk_size=300, chunk_overlap=0, separator=". ")
    redundant_filter = EmbeddingsRedundantFilter(embeddings=embeddings)
    relevant_filter = EmbeddingsFilter(embeddings=embeddings, similarity_threshold=0.76)
    pipeline_compressor = DocumentCompressorPipeline(
        transformers=[splitter, redundant_filter, relevant_filter]
    )
    redis_store = retrieve_redis_vectorstore(vectorstore)
    retriever = redis_store.as_retriever(search_type="similarity", search_kwargs={"k":1000, "score_threshold":"0.2"})

    compression_retriever = ContextualCompressionRetriever(base_compressor=pipeline_compressor, base_retriever=retriever)

    return compression_retriever



def create_elastic_knn():
    # Define the model ID
    model_id = "mymodel"
  # Create Elasticsearch connection
    context = create_default_context(cafile="/home/tebblespc/Downloads/certs.pem")
    es_connection = Elasticsearch(
    hosts=["https://127.0.0.1:9200"], basic_auth=("elastic", "changeme"), ssl_context = context)   

 # Instantiate ElasticsearchEmbeddings using es_connection
    embeddings = ElasticsearchEmbeddings.from_es_connection(
        model_id,
        es_connection,
    )

    query = "Hello"
    knn_result = knn_search.knn_search(query=query, model_id="mymodel", k=2)
    print(f"kNN search results for query '{query}': {knn_result}")
    print(
        f"The 'text' field value from the top hit is: '{knn_result['hits']['hits'][0]['_source']['text']}'"
    )

    # Initialize ElasticKnnSearch
    knn_search = ElasticKnnSearch(
        es_connection=es_connection, index_name="elastic-index", embedding=embeddings
    )
    
    return knn_search


def create_vectorstore(vs_type: str, file: str, file_type: str, index_name: str, embeddings = OpenAIEmbeddings()) -> FAISS or Redis:

    """ Main function used to create any types of vector stores.

    Args:

        vs_type (str): vector store type, "faiss" or "redis"

        file (str): file or directory path

        file_type (str): "dir" or "file"

        index_name (str): name of vector store 

    Returns:

        Faiss or Redis vector store


    """

    try: 
        docs = split_doc(file, file_type, splitter_type="tiktoken")
        if (vs_type=="faiss"):
            db=FAISS.from_documents(docs, embeddings)
            db.save_local(index_name)
            print("Succesfully created Faiss vector store.")
        elif (vs_type=="redis"):
            db = Redis.from_documents(
                docs, embeddings, redis_url=redis_url, index_name=index_name
            )
            print("Successfully created Redis vector store.")
                # db=create_redis_index(docs, embeddings, index_name, source)
    except Exception as e:
        raise e
    return db

        


def retrieve_redis_vectorstore(index_name, embeddings=OpenAIEmbeddings()) -> Redis or None:

    """ Retrieves the Redis vector store if exists, else returns None  """

    try:
        rds = Redis.from_existing_index(
        embeddings, redis_url=redis_url, index_name=index_name
        )
        return rds
    except Exception as e:
        raise e
    


def drop_redis_index(index_name: str) -> None:

    """ Drops the redis vector store with index name. """

    print(Redis.drop_index(index_name, delete_documents=True, redis_url=redis_url))





def merge_faiss_vectorstore(index_name_main: str, file: str, embeddings=OpenAIEmbeddings()) -> FAISS:

    """ Merges files into existing Faiss vecstores if main vector store exists. Else, main vector store is created.

    Args:

        index_name_main (str): name of the main Faiss vector store where others would merge into

        file (str): file path 

    Returns:

        main Faiss vector store 
    
    """
    
    main_db = retrieve_faiss_vectorstore( index_name_main)
    if main_db is None:
        main_db = create_vectorstore("faiss", file, "file", index_name_main)
        print(f"Successfully created vectorstore: {index_name_main}")
    else:
        db = create_vectorstore(embeddings, "faiss", file, "file", "temp")
        main_db.merge_from(db)
        print(f"Successfully merged vectorestore {index_name_main}")
    return main_db
    


def retrieve_faiss_vectorstore(index_name: str, embeddings = OpenAIEmbeddings()) -> FAISS or None:

    """ Retrieves the Faiss vector store if exists, else returns None  """
    
    try:
        db = FAISS.load_local(index_name, embeddings)
        return db
    except Exception as e:
        return None



# def add_embedding(embedding, text):
#     query_embedding = embedding.embed_query(text)
#     return query_embedding


#TODO: handle different types differently
def handle_tool_error(error: ToolException) -> str:

    """ Handles tool exceptions. """

    if error==JSONDecodeError or error.args[0].startswith("Too many arguments to single-input tool"):
        return "Format in Json with correct key and try again."
    return (
        "The following errors occurred during tool execution:"
        + error.args[0]
        + "Please try another tool.")





# # Set up a prompt template
class CustomPromptTemplate(BaseChatPromptTemplate):
    # The template to use
    template: str
    # The list of tools available
    tools: List[Tool]
    
    def format_messages(self, **kwargs) -> str:
        # Get the intermediate steps (AgentAction, Observation tuples)
        # Format them in a particular way
        intermediate_steps = kwargs.pop("intermediate_steps")
        thoughts = ""
        for action, observation in intermediate_steps:
            thoughts += action.log
            thoughts += f"\nObservation: {observation}\nThought: "
        # Set the agent_scratchpad variable to that value
        kwargs["agent_scratchpad"] = thoughts
        # Create a tools variable from the list of tools provided
        kwargs["tools"] = "\n".join([f"{tool.name}: {tool.description}" for tool in self.tools])
        # Create a list of tool names for the tools provided
        kwargs["tool_names"] = ", ".join([tool.name for tool in self.tools])
        formatted = self.template.format(**kwargs)
        return [HumanMessage(content=formatted)]
    
class CustomOutputParser(AgentOutputParser):
    
    def parse(self, llm_output: str) -> Union[AgentAction, AgentFinish]:
        # Check if agent should finish
        if "Final Answer:" in llm_output:
            return AgentFinish(
                # Return values is generally always a dictionary with a single `output` key
                # It is not recommended to try anything else at the moment :)
                return_values={"output": llm_output.split("Final Answer:")[-1].strip()},
                log=llm_output,
            )
        # Parse out the action and action input
        regex = r"Action\s*\d*\s*:(.*?)\nAction\s*\d*\s*Input\s*\d*\s*:[\s]*(.*)"
        match = re.search(regex, llm_output, re.DOTALL)
        if not match:
            raise ValueError(f"Could not parse LLM output: `{llm_output}`")
        action = match.group(1).strip()
        action_input = match.group(2)
        # Return the action and action input
        return AgentAction(tool=action, tool_input=action_input.strip(" ").strip('"'), log=llm_output)
    

    



if __name__ == '__main__':

    db =  create_vectorstore("redis", "./web_data/", "dir", "index_web_advice")
 

    


