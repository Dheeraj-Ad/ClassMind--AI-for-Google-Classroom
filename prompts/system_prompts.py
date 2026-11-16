"""User-visible abstention for the local evidence assistant.

No generative model or system prompt is used in local evidence mode. The engine
retrieves course passages, selects exact quotations, and attaches citations in code.
"""

ABSTENTION = "I couldn't find enough explanatory evidence in your imported sources to answer this question."
