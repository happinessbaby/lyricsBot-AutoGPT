import os
from langchain.chat_models import ChatOpenAI
from langchain.llms import OpenAI
from langchain.embeddings import OpenAIEmbeddings
from langchain.prompts import ChatPromptTemplate
from langchain.output_parsers import CommaSeparatedListOutputParser
from langchain import PromptTemplate
from langchain.agents import AgentType, Tool, initialize_agent, create_json_agent
from basic_utils import read_txt
from common_utils import (get_web_resources, retrieve_from_db, extract_posting_information, extract_education_level, extract_work_experience_level,
                           extract_resume_fields, extract_job_title,  search_related_samples, create_sample_tools, extract_personal_information)
from langchain_utils import create_search_tools, create_mapreduce_chain, create_summary_chain, generate_multifunction_response, create_refine_chain, handle_tool_error
from pathlib import Path
import json
from json import JSONDecodeError
from multiprocessing import Process, Queue, Value
from langchain.tools.json.tool import JsonSpec
from langchain.agents.agent_toolkits import JsonToolkit
from langchain.agents.agent_toolkits import create_python_agent
from langchain.tools.python.tool import PythonREPLTool
from typing import Dict, List, Optional
from langchain.document_loaders import BSHTMLLoader
from langchain.tools import tool
import uuid


from dotenv import load_dotenv, find_dotenv
_ = load_dotenv(find_dotenv()) # read local .env file
resume_evaluation_path = os.environ["RESUME_EVALUATION_PATH"]
resume_samples_path = os.environ["RESUME_SAMPLES_PATH"]
# TODO: caching and serialization of llm
llm = ChatOpenAI(temperature=0.0)
# llm = OpenAI(temperature=0, top_p=0.2, presence_penalty=0.4, frequency_penalty=0.2)
embeddings = OpenAIEmbeddings()
# TODO: save these delimiters in json file to be loaded from .env
delimiter = "####"
delimiter1 = "````"
delimiter2 = "////"
delimiter3 = "<<<<"
delimiter4 = "****"




def evaluate_resume(my_job_title="", company="", resume_file = "", posting_path="") -> str:

    filename = Path(resume_file).stem
    res_path = os.path.join(resume_evaluation_path, filename+".txt")
    res_dir = resume_evaluation_path+filename
    try:
      os.mkdir(res_dir)
    except FileExistsError:
      pass
    # stores basic extracted responses, like education level, in a dictionary
    generated_responses = {}

    # get resume
    resume = read_txt(resume_file)
    # extract personal information
    personal_info_dict = extract_personal_information(resume)
    generated_responses.update(personal_info_dict)
    # get resume field names and field content
    field_names, field_content = extract_resume_fields(resume)
    # generated_responses.update( {"resume_fields)

    job_specification = ""
    # get job specification and company name from job posting link, if provided
    if (Path(posting_path).is_file()):
      prompt_template = """Identity the job position, company then provide a summary of the following job posting:
        {text} \n
        Focus on roles and skills involved for this job. Do not include information irrelevant to this specific position.
      """
      job_specification = create_summary_chain(posting_path, prompt_template)
      posting = read_txt(posting_path)
      posting_info_dict=extract_posting_information(posting)
      my_job_title = posting_info_dict["job"]
      company = posting_info_dict["company"]

    # if job title is not provided anywhere else, extract from the resume
    if (my_job_title==""):
      my_job_title = extract_job_title(resume)
    generated_responses.update({"job title": my_job_title})
    generated_responses.update({"company name": company})
    generated_responses.update({"job specification": job_specification})

    # get general job description
    query_job  = f"""Research what a {my_job_title} does, including details of the common skills, responsibilities, education, experience needed for the job."""
    job_description = get_web_resources(query_job)
    generated_responses.update({"job description": job_description})

    # get company description, if provided
    company_description=""
    if (company!=""):
      company_query = f""" Research what kind of company {company} is, such as its culture, mission, and values.                         
                          Look up the exact name of the company. If it doesn't exist or the search result does not return a company, output you don't know"""
      company_description = get_web_resources(company_query)
    generated_responses.update({"company description": company_description})

    # get highest level of education
    education_level = extract_education_level(resume)
    # get work experience level wrt the job position
    # work_experience = extract_work_experience_level(resume, my_job_title)
    work_experience = ""

    # samples query to find missing content from resume using document comparison
    # Note: document comparison benefits from a clear and simple prompt
    related_samples = search_related_samples(my_job_title, resume_samples_path)
    sample_tools, tool_names = create_sample_tools(related_samples, "resume")
    query_sample = f"""  You are an expert resume advisor that specializes in comparing exemplary sample resume. 

    Sample resume are provided in your tools. Research {str(tool_names)} and answer the following question:

       What field information do these resume share, or what content do they have in common? 

    Please do not list your answer but write in complete sentences as an expert resume advisor. 

    Your answer should be general without losing details. For example, you should not compare company names but should compare shared work experiences.

    """
    commonality = generate_multifunction_response(query_sample, sample_tools)
    # Note: retrieval query works best if clear, concise, and detail-dense
    advice_query = f"""what to include for resume with someone with {education_level} and {work_experience} for {my_job_title}"""
    advices = retrieve_from_db(advice_query)

    query_missing = f""" You are an expert resume advisor that specializes in finding missing fields and content. 

        Your job is to find missing items in the resume delimiter with {delimiter} characters using expert suggestions. 

        Ignore all formatting advices and any specific details such as personal details, dates, schools, companies, affiliations, hobbies, etc. 
        
        Your answer should be general enough to allow applicant to fill out the missing content with their own information. 

        Please output only the missing field names and/or missing field content in a list. Do not provide the source. 

        expert suggestion: {advices} \n {commonality} \n

        resume: {delimiter}{resume}{delimiter}. """

    missing_items = generate_multifunction_response(query_missing, sample_tools)
    file_path = file_path = os.path.join(res_dir, "missing.txt")
    with open(file_path, "w") as f:
      f.write(missing_items)



    #TODO: this can be a general putpose tool with all web data
    general_tools = create_search_tools("google", 3)
    # relevancy_tools = [search_relevancy_advice]

    files = []
    for field in field_names:
      file_path = os.path.join(res_dir, f"{field}.txt")
      improve_resume_fields(generated_responses, field, field_content.get(field, ""),  general_tools, file_path)
      files.append(file_path)

    #TODO generate a reporter agent that can summarize everything without losing any details
    # main_template = """Write a detailed summary of the following: {text}
    #     DETAILED SUMMARY: """
    # refine_template = (
    #     "Your job is to produce a final summary\n"
    #     "We have provided an existing summary up to a certain point: {existing_answer}\n"
    #     "We have the opportunity to refine the existing summary"
    #     "(only if needed) with some more context below.\n"
    #     "------------\n"
    #     "{text}\n"
    #     "------------\n"
    #     "Given the new context, refine the original summary."
    #     "If the context isn't useful, return the original summary."
    # )
    # response = create_refine_chain(files, main_template, refine_template)
    # return response
    # return create_mapreduce_chain(files)






    # process all fields in parallel
    # processes = [Process(target = improve_resume_fields, args = (generated_responses, field, resume, res_path, relevancy_tools, sample_tools)) for field in resume_fields]

    # start all processes
    # for p in processes:
    #    p.start()

    # for p in processes:
    #    p.join()

    # return result to chat agent
    return f""" file:{os.path.join(res_dir, "missing.txt")} """


def improve_resume_fields(generated_response: Dict[str, str], field: str, field_content: str, general_tools: List[Tool], file_path: str) -> None:

    print(f"CURRENT FIELD IS: {field}")
    my_job_title = generated_response.get("job title", "")
    company_description = generated_response.get("company description", "")
    job_specification = generated_response.get("job specification", "")
    job_description = generated_response.get("job description", "")
    # education_level = generated_response.get("education", "")

    # The purpose of getting expert advices is to evaluate weaknesses in the current resume field content
    advice_query =  f"""What are some dos and don'ts when writing resume field {field} to make it ATS-friendly?"""
    advices = retrieve_from_db(advice_query)
    query_evaluation = f""" You are an expert resume advisor. You are given some expert advices on writing {field} section of the resume.
    
    advices: {advices}

    Use these advices to identity the strenghts and weaknesses of the resume content delimited with {delimiter} characters. 
    
    field content: {delimiter}{field_content}{delimiter}. \n
    
    Please provide proof of your reasoning. For example, if there is gap in work history, mark it as a weakness unless there is evidence it should not be considered so.
    
    Ignore all formatting. They should not be considered as weaknesses or strengths. 

    """
    strength_weakness = generate_multifunction_response(query_evaluation, general_tools)

    # Get resume's relevant and irrelevant info for resume field: few-shot learning works great here
    # The purpose of identitying irrelevant and relevant information is so that irrelevant information can be deleted or reworded
    query_relevancy = f"""You are an expert resume advisor. Determine the relevant and irrelevant information contained in the field content. 

     Generate a list of irrelevant information that should not be included in the cover letter and a list of relevant information that should be included in the resume field.
       
      Remember to use either job specification or general job description as your guideline. Don't forget to use your tool "search_relevancy_advice".

      field name: {field}

      field content: {field_content}\n

      job specification: {job_specification}\n

      general job description: {job_description} \n

      Your answer should be detailed and only from the resume. Please also provide your reasoning too. 
        
      For example, your answer may look like this:

      Relevant information in the Experience field:

      1. Experience as a Big Data Engineer: skills and responsibilities of a Big Data Engineer are transferable to those of a data analyst

      Irrelevant information  in the Experience Field:

      1. Experience as a front desk receptionist is not directly related to the role of a data analyst

      You do not need to evaluate field that contains personal information. 

      """

    relevancy = generate_multifunction_response(query_relevancy, general_tools)


    # template_string = """ You are a professional report writer. Your task is to polish a professional's report and present it to the Human in a clear and Human-readable manner.

    # Here is the professional report: {strength_weakness} + \n + {relevancy} \n

    # Please don't leave out any details in your report.  """


    # prompt_template = ChatPromptTemplate.from_template(template_string)
    # upgrade_resume_message = prompt_template.format_messages(
    #     relevancy = relevancy,
    #     strength_weakness = strength_weakness,
    # )

    # my_advice = llm(upgrade_resume_message).content

    with open(file_path, "w") as f:
       f.write(strength_weakness + '\n' + relevancy + '\n')

    # return strength_weakness + '\n' + relevancy + '\n'


# @tool("resume evaluator")
# def resume_evaluator_tool(resume_file: str, job: Optional[str]="", company: Optional[str]="", job_post_link: Optional[str]="") -> str:

#    """Evaluate a resume when provided with a resume file, job, company, and/or job post link.
#         Note only the resume file is necessary. The rest are optional.
#         Use this tool more than any other tool when user asks to evaluate, review, help with a resume. """

#    return evaluate_resume(my_job_title=job, company=company, resume_file=resume_file, posting_path=job_post_link)

@tool
def resume_evaluator(json_request: str)-> str:

    """Helps to evaluate a resume. Use this tool more than any other tool when user asks to evaluate, review, help with a resume. 

    Input should be  a single string strictly in the following JSON format:  '{{"job":"<job>", "company":"<company>", "resume file":"<resume file>", "job post link": "<job post link>"}}' \n

    Output should be calling the 'file_loader' tool in the followng JSON format: '{{"file": "<file>"}}' \n 
    
    """

    try:
      json_request = json_request.strip("'<>() ").replace('\'', '\"')
      args = json.loads(json_request)
    except JSONDecodeError as e:
      print(f"JSON DECODE ERROR: {e}")
      return "Format in a single string JSON and try again."


    # if resume doesn't exist, ask for resume
    if ("resume file" not in args or args["resume file"]=="" or args["resume file"]=="<resume file>"):
      return "Can you provide your resume so I can further assist you? "
    else:
      # may need to clean up the path first
        resume_file = args["resume file"]
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

    return evaluate_resume(my_job_title=job, company=company, resume_file=resume_file, posting_path=posting_path)







# def processing_resume(json_request: str) -> str:

#     """ Input parser: input is LLM's action_input in JSON format. This function then processes the JSON data and feeds them to the resume evaluator. """

#     try:
#       json_request = json_request.strip("'<>() ").replace('\'', '\"')
#       args = json.loads(json_request)
#     except JSONDecodeError as e:
#       print(f"JSON DECODER ERROR: {e}")
#       return "Format in JSON and try again."

#     # if resume doesn't exist, ask for resume
#     if ("resume file" not in args or args["resume file"]=="" or args["resume file"]=="<resume file>"):
#       return "Can you provide your resume so I can further assist you? "
#     else:
#       # may need to clean up the path first
#         resume_file = args["resume file"]
#     if ("job" not in args or args["job"] == "" or args["job"]=="<job>"):
#         job = ""
#     else:
#        job = args["job"]
#     if ("company" not in args or args["company"] == "" or args["company"]=="<company>"):
#         company = ""
#     else:
#         company = args["company"]
#     if ("job post link" not in args or args["job post link"]=="" or args["job post link"]=="<job post link>"):
#         posting_path = ""
#     else:
#         posting_path = args["job post link"]

#     return evaluate_resume(my_job_title=job, company=company, resume_file=resume_file, posting_path=posting_path)


# def create_resume_evaluator_tool() -> List[Tool]:

#     """ Input parser: input is user's input as a string of text. This function takes in text and parses it into JSON format. 
    
#     Then it calls the processing_resume function to process the JSON data. """

#     name = "resume_evaluator"
#     parameters = '{{"job":"<job>", "company":"<company>", "resume file":"<resume file>", "job post link": "<job post link>"}}'
#     output = '{{"resume evaluation file": "<resume evaluation file>"}}'
#     description = f"""Helps to evaluate a resume. Use this tool more than any other tool when user asks to evaluate, review, help with a resume. 
#     Input should be a single string strictly in the following JSON format: {parameters} \n
#     Output should be calling the 'file_loader' tool in the followng JSON format: {output} \n
#     """
#     tools = [
#         Tool(
#         name = name,
#         func = processing_resume,
#         description = description,
#         verbose = False,
#         handle_tool_error=handle_tool_error,

#         )
#     ]
#     print("Succesfully created resume evaluator tool.")
#     return tools





if __name__ == '__main__':
    my_job_title = 'accountant'
    my_resume_file = 'resume_samples/sample1.txt'
    # evaluate_resume()


