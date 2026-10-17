hs = {}
if obj.status ~= nil then
  -- 1. If the child app is actively computing or has an unfinished operation, force it to wait
  if obj.status.operationState ~= nil and obj.status.operationState.phase == "Running" then
    hs.status = "Progressing"
    hs.message = "Workloads are actively synchronizing..."
    return hs
  end

  -- 2. Check if the child app is out of sync with Git
  if obj.status.sync ~= nil and obj.status.sync.status == "OutOfSync" then
    hs.status = "Progressing"
    hs.message = "Application is OutOfSync. Waiting for sync to complete."
    return hs
  end

  -- 3. Fall back to standard health values if git state is clean
  if obj.status.health ~= nil then
    hs.status = obj.status.health.status
    if obj.status.health.message ~= nil then
      hs.message = obj.status.health.message
    end
    return hs
  end
end

hs.status = "Progressing"
hs.message = "Waiting for application status to populate..."
return hs