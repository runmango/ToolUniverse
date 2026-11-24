"""Shared source adapter; model inference endpoints are separate from medical sources."""
import uuid
from .db import digest, encoded, now
from ..tools import logical_error

class MedicalSources:
    version='tooluniverse-medical-v1'
    def __init__(self,tools,store): self.tools,self.store=tools,store
    async def load(self): return await self.tools.load()
    async def run(self,name,arguments):
        identity=uuid.uuid4().hex; response=None; error=None
        try:
            response=await self.tools.run(name,arguments)
            failure=logical_error(response)
            if failure: raise RuntimeError(failure)
            return response
        except Exception as exc:
            error=type(exc).__name__
            raise
        finally:
            with self.store.connect() as db:
                db.execute('INSERT INTO research_source_fetches VALUES (?,?,?,?,?,?,?,?,?)',
                           (identity,self.version,name,encoded(arguments),digest(response) if response is not None else None,
                            encoded(response) if response is not None else None,'failed' if error else 'completed',error,now()))
