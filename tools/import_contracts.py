"""Собственные типы контрактов import-linter.

Подключаются в pyproject.toml через `contract_types`. Нужен параметр
`include_external_packages = true`: иначе в графе нет стандартной библиотеки
и сторонних пакетов.
"""

import sys

from grimp import ImportGraph
from importlinter import Contract, ContractCheck, fields, output


class StdlibOnlyContract(Contract):
    """Модули source_modules импортируют только стандартную библиотеку и самих себя.

    Это список разрешённого: всё, чего нет в `sys.stdlib_module_names`
    и что лежит вне source_modules, нарушает контракт — и сторонние пакеты,
    и другие пакеты проекта.
    """

    type_name = "stdlib_only"

    source_modules = fields.ListField(subfield=fields.ModuleField())

    def check(self, graph: ImportGraph, verbose: bool) -> ContractCheck:
        sources = [module.name for module in self.source_modules]  # type: ignore[attr-defined]
        violations = []
        for source in sources:
            for importer in sorted({source} | graph.find_descendants(source)):
                for imported in sorted(graph.find_modules_directly_imported_by(importer)):
                    if self._is_allowed(imported, sources):
                        continue
                    details = graph.get_import_details(importer=importer, imported=imported)
                    violations.append(
                        {
                            "importer": importer,
                            "imported": imported,
                            "line_numbers": [detail["line_number"] for detail in details],
                        }
                    )
        return ContractCheck(kept=not violations, metadata={"violations": violations})

    def render_broken_contract(self, check: ContractCheck) -> None:
        for violation in check.metadata["violations"]:
            lines = ", ".join(f"l.{number}" for number in violation["line_numbers"])
            output.print_error(
                f"{violation['importer']} -> {violation['imported']} ({lines})",
                bold=False,
            )
        output.new_line()

    @staticmethod
    def _is_allowed(imported: str, sources: list[str]) -> bool:
        if any(imported == source or imported.startswith(f"{source}.") for source in sources):
            return True
        return imported.split(".")[0] in sys.stdlib_module_names
