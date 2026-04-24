# Create a Modelfile that enables thinking mode and sets a custom system prompt
cat > ~/Modelfile.qwen35-docs << 'EOF'
FROM qwen3.5:9b
 
PARAMETER temperature 0.3
PARAMETER top_k 20
PARAMETER top_p 0.95
PARAMETER presence_penalty 1.5
PARAMETER num_ctx 32768
 
SYSTEM """
You are a technical documentation assistant. When given source code files, you:
1. Generate accurate Mermaid.js diagrams (flowchart TD or sequenceDiagram syntax only)
2. Produce concise module summaries with inputs, outputs, and side effects
3. Write a system overview linking all modules together
4. Output ONLY valid JSON matching the schema provided in the user message
5. Never hallucinate function names, types, or dependencies not present in the code
"""
EOF
 
# Build the custom model
ollama create qwen35-docs -f ~/Modelfile.qwen35-docs
 
# Test it
ollama run qwen35-docs "List the files you would expect in a Node.js REST API project"