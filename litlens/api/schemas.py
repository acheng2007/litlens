from pydantic import BaseModel, Field

class LibraryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    owner_id: str = "local"

class SearchRequest(BaseModel):
    library_id: str
    query: str = Field(min_length=1)
    limit: int = Field(default=8, ge=1, le=50)

class AnswerRequest(SearchRequest): pass

