import os
from openai_api import check_content_safety
from langchain.chat_models import ChatOpenAI
from langchain.llms import OpenAI
from langchain.embeddings import OpenAIEmbeddings
from langchain.prompts import ChatPromptTemplate
from langchain.output_parsers import CommaSeparatedListOutputParser
from langchain import PromptTemplate
from langchain.agents import AgentType, Tool, initialize_agent
from basic_utils import read_txt
from common_utils import compare_samples, get_web_resources, retrieve_from_db, get_job_relevancy, extract_posting_information, get_summary, extract_fields, get_field_content, extract_job_title
from langchain_utils import retrieve_faiss_vectorstore, create_vectorstore, merge_faiss_vectorstore
from langchain.text_splitter import MarkdownHeaderTextSplitter
from pathlib import Path
import json
from base import base


from dotenv import load_dotenv, find_dotenv
_ = load_dotenv(find_dotenv()) # read local .env file

# TBD: caching and serialization of llm
llm = ChatOpenAI(temperature=0.0, cache=False)
# llm = OpenAI(temperature=0, top_p=0.2, presence_penalty=0.4, frequency_penalty=0.2)
embeddings = OpenAIEmbeddings()
# randomize delimiters from a delimiters list to prevent prompt injection
delimiter = "####"
delimiter1 = "````"
delimiter2 = "////"
delimiter3 = "<<<<"
delimiter4 = "****"

my_job_title = 'accountant'
my_resume_file = 'resume_samples/sample1.txt'
resume_advice_path = './web_data/resume/'
resume_samples_path = './resume_samples/'
posting_path = "./uploads/posting/accountant.txt"


    
def evaluate_resume(my_job_title="", company="", read_path = my_resume_file, posting_path=""):
    
    res_path = os.path.join("./static/advice/", Path(read_path).stem + ".txt")
    print(res_path)

    resume = read_txt(read_path)

    resume_fields = extract_fields(resume)

    resume_fields = resume_fields.split(",")
    print(f"{resume_fields}")

    job_specification = ""
    if (Path(posting_path).is_file()):
      job_specification = get_summary(posting_path)
      posting = read_txt(posting_path)
      posting_info_dict=extract_posting_information(posting)
      my_job_title = posting_info_dict["job"]
      company = posting_info_dict["company"]

    if (my_job_title==""):
      my_job_title = extract_job_title(resume)
 
    query_job  = f"""Research what a {my_job_title} does, including details of the common skills, responsibilities, education, experience needed for the job."""
    job_description = get_web_resources(query_job, "google")

    company_description=""
    if (company!=""):
      company_query = f""" Research what kind of company {company} is, such as its culture, mission, and values.
                          
                          Look up the exact name of the company. If it doesn't exist or the search result does not return a company, output you don't know"""
      
      company_description = get_web_resources(company_query, "wiki")


    for field in resume_fields:

      print(f"CURRENT FIELD IS: {field}")

      field_content = get_field_content(resume, field)
        
      
      query_relevancy = f"""Determine the relevant and irrelevant information contained in the resume field.

        You are  provided with job specification for an opening position. 
        
        Use it as a primarily guidelines when generating your answer. 

        You are also provided with a general job decription of the requirement of {my_job_title}. 
        
        Use it as a secondary guideline when forming your answer.

        If job specification is not provided, use general job description as your primarily guideline. 


        reusme field: {field_content}\n

        job specification: {job_specification}\n

        general job description: {job_description} \n


        Generate a list of irrelevant information that should not be included in the resume and a list of relevant information that should be included in the field. 

          """
      relevancy = get_job_relevancy(read_path, query_relevancy)

      query_advice =  f"how to best wriite {field} for resume?"

      resume_advices = retrieve_from_db(query_advice)

      query_samples = f""" 
        Research sample resume provided. 

        If the resume contains a field that's related to {field}, answer the following question. Otherwise, ignore the questions: 

        1. common noun keywords 

        2. common action keywords

        """
      # practices = compare_samples(my_job_title,  query_samples, resume_samples_path, "resume")


      template_string = """" Your task is to analyze and help improve the content of resume field {field}. 

      The content of the field is delimiter with {delimiter} characters. Always use this as contenxt and do not make things up. 

      field content: {delimiter}{field_content}{delimiter}
      

      Step 1: You're given some expert advices on how to write {field} . Keep these advices in mind for the next steps.

          expert advices: {delimiter1}{advices}{delimiter1}  \n


      step 2: You are given two lists of information delimited with {delimiter2} characters. One is content to be included in the {field} and the other is content to be removed. 

          Use them as to generate your answer. 

          information list: {delimiter2}{relevancy}{delimiter2} \n

      Step 3: You're provided with some company informtion and job specification

        Look for ATS-friendly keywords in the job specification and company information to make the resume field cater to the compnay and job position. 

        If company information and/or job specifcation do not pertain to the resume field, skip this step. 

        company information: {company_description}.  \n

        job specification: {job_specification}.    \n

      Step 4: Based on what you gathered in Step 1 through 3, rewrite the resume field {field}. Do not make up things. 

      Use the following format:
          Step 1:{delimiter4} <step 1 reasoning>
          Step 2:{delimiter4} <step 2 reasoning>
          Step 3:{delimiter4} <step 3 reasoning>
          Step 4:{delimiter4} <rewrite the resume field>


        Make sure to include {delimiter4} to separate every step.
      
      """

      prompt_template = ChatPromptTemplate.from_template(template_string)
      upgrade_resume_message = prompt_template.format_messages(
          field = field,
          field_content = field_content,
          job = my_job_title,
          advices=resume_advices,
          relevancy = relevancy,
          company_description = company_description,
          job_specification = job_specification, 
          delimiter = delimiter,
          delimiter1 = delimiter1, 
          delimiter2 = delimiter2,
          delimiter4 = delimiter4, 
          
      )
      my_advice = llm(upgrade_resume_message).content

      # Check potential harmful content in response
      if (check_content_safety(text_str=my_advice)):   
          if (postprocessing(my_advice, res_path)):
              create_resume_advice_doc_tool(res_path)


def postprocessing(response, res_path):
    
    # TODO: user markdownheadersplitter to split according to delimiters before vs storage

    # cut the text to only cover letter
      # transform_chain = TransformChain(
    #     input_variables=["text"], output_variables=["output_text"], transform=transform_func)
    # stream out the answer
    # chat = ChatOpenAI(streaming=True, callbacks=[StreamingStdOutCallbackHandler()], temperature=0)
    # langchain.llm_cache = InMemoryCache()

    with open(res_path, 'w') as f:
        try:
            f.write(response)
            print("ALL SUCCESS")
            return True
        except Exception as e:
            print("FAILED")
            return False



# receptionist
def preprocessing(json_request):
    print(json_request)
    args = json.loads(json_request)
    # if resume doesn't exist, ask for resume
    if ("resume file" not in args or args["resume file"]=="" or args["resume file"]=="<resume file>"):
      return "Can you provide your resume so I can further assist you? "
    else:
      # may need to clean up the path first
        read_path = args["resume file"]
    if ("job" not in args or args["job"] == "" or args["job"]=="<job>"):
        job = ""
    else:
       job = args["job"]
    if ("company" not in args or args["company"] == "" or args["company"]=="<company>"):
        company = ""
    else:
        company = args["company"]
    if ("job post link" not in args or args["job post link"]=="" or args["job post link"]=="<job post link>"):
        posting_path = ""
    else:
        posting_path = args["job post link"]
    res = evaluate_resume(my_job_title=job, company=company, read_path=read_path, posting_path=posting_path)
    return res

def create_resume_evaluator_tool():
    name = "resume evaluator"
    parameters = '{{"job":"<job>", "company":"<company>", "resume file":"<resume file>", "job post link": "<job post link>"}}'
    description = f"""Helps to evaluate a resume. Use this tool more than any other tool when user asks to evaluate, review, help with a resume. 
    Input should be JSON in the following format: {parameters} \n
    """
    tools = [
        Tool(
        name = name,
        func = preprocessing,
        description = description, 
        verbose = False,
        )
    ]
    print("Succesfully created resume evaluator tool.")
    return tools


def create_resume_advice_doc_tool(read_path):   
    userid = Path(read_path).stem
    name = "faiss_resume_advice"
    description = """This is user's detailed resume advice. If this tool exists, do not use the 'resume evaluator' tool anymore. 
    Use this tool as a reference to give tailored resume advices. """
    create_vectorstore(embeddings, "faiss", read_path, "file",  f"{name}_{userid}")
    chat = base.get_chat()
    chat.add_tools(userid, name, description)


def test_resume_tool():
    
    tools = create_resume_evaluator_tool()
    agent= initialize_agent(
        tools, 
        llm=ChatOpenAI(cache=False), 
        agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION,
        handle_parsing_errors=True,
        verbose = True,
        )
    response = agent.run(f"""evaluate a resume with following information:
                              job:  \n
                              company:  \n
                              resume file: {my_resume_file} \n
                              job post links: \n            
                              """)
    return response
   
   


if __name__ == '__main__':
    # evaluate_resume()
    test_resume_tool()
 