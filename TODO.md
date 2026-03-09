#TODOs

### Stage 1:

Get the app to a Element hosted stage that can securely be used by CT in POC mode.

- [ ] RL - Create docker / Kubernetes deployment
  - [ ] Deploy backend scripts
  - [ ] Deploy web UI
- [ ] AG - Create a web UI to upload input doc, initiate processing and download output.
- [ ] RL - Update LLM from Anthropic -> Cloud hosted
- [ ] RL - Some sort of auth (KeyCloak) / not exposed to the public internet?
  - [ ] RL - Hard-code some auth credentials
- [ ] ~~Replace / implement Brave Search (What is the best tool that we can use for this)~~
  - [ ] AG to check into costs and to share API key with RL
  - Can we use the current Brave search API / free tier
  - Are we near the free usage tier limits at the moment?
    - It appears that we're not near the limit at the moment (AG) / March 9th
      - 194 requests so far this month / two tests (sounds like we get 2000 / month, so 20 processes per month)
  - [ ] AG - Deploy "as is" and test functionality
