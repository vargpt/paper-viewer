```markdown
# Bringing Research Papers To Life Team: 1D Tensors, (i.e., the vectors) (Team 4)

Members: Varun Gupta, Sara Ketabi, Salamata Konate, Sai Tedla
Repo: https://github.com/vargpt/paper-viewer 

## 1. Task
Research papers are often not the correct medium for interpreting research. Papers are written for general audiences and are restricted to the format of text and stationary images or diagrams. This traditional format limits learning and fundamentally hinders scientific progress and communication. To address these issues, we propose an agentic framework that converts arbitrary research papers into interactive webpages. 

## 2. Why an agent?
An agentic framework is needed because transforming a research paper into an interactive webpage requires decisions that cannot be fully predetermined. We propose a multi-agent architecture with a master agent responsible for analyzing the paper, identifying its different sections, and dynamically delegating subtasks to specialized agents based on the content. For example, mathematical sections could be assigned to an agent specialized in explaining equations and generating interactive mathematical visualizations, while literature reviews could be handled by an agent focused on synthesizing related work and illustrating connections between studies. Other agents could specialize in experimental results, methodology, or interactive webpage generation.
The master agent would coordinate these agents, integrate their outputs into a coherent webpage, and evaluate whether the explanations and visualizations accurately represent the original research. It could also request revisions or delegate additional tasks when necessary. This approach allows the system to adapt its strategy to each paper rather than following a fixed workflow, ultimately producing more accurate, accessible, and engaging explanations tailored to the paper's content.

## Tools and data
Tools
- Docling: Used by the master agent to extract and structure text, equations, figures, and tables from research papers.
- Semantic Scholar API: Used by the literature review agent to retrieve related research papers, references, and citations.
- SymPy: Used by the mathematics agent to analyze, simplify, and verify mathematical 
equations.
- Plotly: Used by the visualization agent to generate interactive graphs and
visualizations that explain scientific concepts.
- Playwright: Used by the master agent or validation agent to test the generated HTML
webpage and verify that interactive elements function correctly.
Data source: User uploaded research papers (PDFs), publicly available scientific literature from:
	•	Alphaxiv MCP server
	•	Google scholar 
	•	Arxiv

## 4. Baseline
The baseline will be a single LLM call without access to tools or subagents that generates an output to explain the input paper. We will evaluate two baselines -- two different models of varying capability.

## 5. Evaluation
The evaluation will be conducted jointly by LLM judges and human experts (i.e., team members). We will have 8 test cases -- 2 papers selected by each team member -- which the team has expertise in. The LLM judges and human experts will assign a score to each method (baseline and agentic system), where each method is evaluated using the best of 3 runs for each test case. The evaluation protocol will measure the number of hallucinations, consistency with the original paper (scored 1-10), and the quality of the explanations (scored 1-10) as rated from the perspective of viewers from different age brackets.  

## 6. Demo
The demo will involve taking live suggestions (or preselected papers) from the audience for papers that they want explained and then the agentic system will produce an explanation / visualization of the selected papers. 

## 7. Risks, Limits, Roles
Risks include that this task is not easily verifiable and consequently it is difficult to evaluate, especially for test cases that are not within the team member’s areas of expertise. Using an LLM as a judge allows us to evaluate these test cases, but partially just defers the evaluation question since we don’t evaluate the LLM judges. We aim to mitigate low quality evaluations by using multiple LLM judges. 
```
