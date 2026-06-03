## Inventory Frontend JSX Files
Assign to: research_agent
Files: src/**/*.jsx
Deps: []
Description: Recursively scan the entire frontend source directory (e.g., src/) and list all files ending with the .jsx extension. The output must be a comprehensive list of file paths.

## Summarize Code Contents
Assign to: code_agent
Files: (Output of T1)
Deps: [T1]
Description: For every file path provided in T1, read the file content. Generate a detailed, yet concise, summary for each file, including its primary purpose, the major components it renders, and a brief explanation of its core logic or state management usage.

## Generate Final Report
Assign to: research_agent
Files: None
Deps: [T2]
Description: Compile all the individual summaries generated in T2 into a single, high-level markdown report. This report must provide a holistic overview of the entire frontend architecture, grouping related files and summarizing the overall flow of data and components across the application.