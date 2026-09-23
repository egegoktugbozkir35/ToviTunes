"""Learning objectives are stable curriculum data, not generated prose."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CurriculumConcept(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    concept_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    objective_id: str
    objective: str
    target_vocabulary: tuple[str, ...] = Field(min_length=1)


class Curriculum(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    curriculum_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: str
    concepts: tuple[CurriculumConcept, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_concepts(self) -> "Curriculum":
        ids = [item.concept_id for item in self.concepts]
        objectives = [item.objective_id for item in self.concepts]
        if len(ids) != len(set(ids)) or len(objectives) != len(set(objectives)):
            raise ValueError("curriculum concept and objective IDs must be unique")
        return self

    def get(self, concept_id: str) -> CurriculumConcept:
        for concept in self.concepts:
            if concept.concept_id == concept_id:
                return concept
        raise KeyError(f"unknown curriculum concept: {concept_id}")


class CurriculumRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision_id: str
    curriculum_id: str
    version: str
    sha256: str

