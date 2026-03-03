"""
Pymoo integration layer for neural-symbolic satellite layout optimization.

This package adds a non-disruptive meta-optimization path:
1. Encode `DesignState` into optimization vectors.
2. Build `ElementwiseProblem` dynamically from task specs.
3. Run feasibility-first NSGA-II and return structured diagnostics.
"""

from .specs import (
    ConstraintSpec,
    ObjectiveSpec,
    PymooProblemSpec,
    SemanticZone,
    VariableSpec,
    default_constraint_specs,
    default_objective_specs,
)
from .codec import DesignStateVectorCodec
from .operator_program_codec import OperatorProgramGenomeCodec
from .repair import CentroidPushApartRepair
from .problem_generator import PymooProblemGenerator, synthesize_problem_class_code
from .operator_problem_generator import OperatorProgramProblemGenerator
from .runner import (
    PymooExecutionResult,
    PymooMOEADRunner,
    PymooNSGA2Runner,
    PymooNSGA3Runner,
    calculate_aocc,
)
from .code_executor import ScriptExecutionResult, safe_exec_generated_script

__all__ = [
    "VariableSpec",
    "ObjectiveSpec",
    "ConstraintSpec",
    "SemanticZone",
    "PymooProblemSpec",
    "default_objective_specs",
    "default_constraint_specs",
    "DesignStateVectorCodec",
    "OperatorProgramGenomeCodec",
    "CentroidPushApartRepair",
    "PymooProblemGenerator",
    "OperatorProgramProblemGenerator",
    "synthesize_problem_class_code",
    "PymooExecutionResult",
    "PymooNSGA3Runner",
    "PymooMOEADRunner",
    "PymooNSGA2Runner",
    "calculate_aocc",
    "ScriptExecutionResult",
    "safe_exec_generated_script",
]
