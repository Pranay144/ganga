#\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\#
"""The Ganga backendhandler for the Dirac system."""

#\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\#
import os, re, fnmatch
from Ganga.GPIDev.Adapters.StandardJobConfig import StandardJobConfig
from Ganga.GPIDev.Schema                     import Schema, Version, SimpleItem
from Ganga.GPIDev.Adapters.IBackend          import IBackend
from Ganga.Core                              import BackendError, GangaException
from GangaDirac.Lib.Backends.DiracUtils      import *
from GangaDirac.Lib.Server.WorkerThreadPool  import WorkerThreadPool
from Ganga.Utility.ColourText                import getColour
from Ganga.Utility.Config                    import getConfig
from Ganga.Utility.logging                   import getLogger
logger = getLogger()
regex  = re.compile('[*?\[\]]')
dirac_ganga_server      = WorkerThreadPool()
dirac_monitoring_server = WorkerThreadPool()

class DiracBase(IBackend):
    """The backend that submits jobs to the Grid via DIRAC.
    
    The backend for jobs to be submitted to the Grid. Jobs are
    submitted through the DIRAC WMS system and then in turn submitted to the
    Grid. A few examples of usage are given below
        
    # Create Dirac backend object
    b = Dirac()
    
    # Create and submit job.
    j = Job(application=app,backend=b)
    j.submit()
    
    # Run a Root job on the Grid if in LHCb VO
    
    # Create a Root application object. See Root help text for instructions
    # on how to configure this.
    app = Root()
    
    # Create and submit job to Dirac using default options
    j = Job(application=app,backend=Dirac())
    j.submit()
    
    # Using the 'settings' attribute
    j.backend.settings['BannedSites'] = ['LCG.CERN.ch']
    j.resubmit()
    
    # settings can be set at any time but are only 'respected' during
    # submit and resubmit.
    
    """
       
    dirac_monitoring_is_active = True
    
    _schema = Schema(Version(3, 2),{
        'id'          : SimpleItem(defvalue=None, protected=1, copyable=0,
                                   typelist=['int','type(None)'],
                                   doc='The id number assigned to the job by the DIRAC WMS. If seeking help'\
                                       ' on jobs with the Dirac backend, please always report this id ' \
                                       'number in addition to a full description of your problem. The id '\
                                       'can also be used to further inspect the job at ' \
                                       'https://lhcbweb.pic.es/DIRAC/info/general/diracOverview'),
        'status'      : SimpleItem(defvalue=None, protected=1, copyable=0,
                                   typelist=['str','type(None)'],
                                   doc='The detailed status as reported by the DIRAC WMS'),
        'actualCE'    : SimpleItem(defvalue=None, protected=1, copyable=0,
                                   typelist=['str','type(None)'],
                                   doc='The location where the job ran'),
        'normCPUTime' : SimpleItem(defvalue=None, protected=1, copyable=0,
                                   typelist=['str','type(None)'],
                                   doc='The normalized CPU time reported by the DIRAC WMS'),
        'statusInfo'  : SimpleItem(defvalue='', protected=1, copyable=0,
                                   typelist=['str','type(None)'],
                                   doc='Minor status information from Dirac'),
        'diracOpts'   : SimpleItem(defvalue='',
                                   doc='DIRAC API commands to add the job definition script. Only edit ' \
                                       'if you *really* know what you are doing'),
        'settings'    : SimpleItem(defvalue={'CPUTime':2*86400},
                                   doc='Settings for DIRAC job (e.g. CPUTime, BannedSites, etc.)')
        })
    _exportmethods = ['getOutputData','getOutputSandbox','removeOutputData',
                      'getOutputDataLFNs','peek','reset','debug']
    _packed_input_sandbox = True
    _category = "backends"
    _name = 'DiracBase'
    _hidden = True
    
    def master_prepare(self, masterjobconfig):
        def filt(sharedsandbox):
            if sharedsandbox:
                def shareboxfilter(item):
                    return item.name.find(self.getJobObject().application.is_prepared.name) != -1
                return shareboxfilter
            
            def nonshareboxfilter(item):
                return item.name.find(self.getJobObject().application.is_prepared.name) == -1
            return nonshareboxfilter
        
        
        if masterjobconfig:
            inputsandbox  = [f.name for f in filter(filt(True) , masterjobconfig.getSandboxFiles())]
            sjc = StandardJobConfig(inputbox=filter(filt(False), masterjobconfig.getSandboxFiles()))
            if sjc.getSandboxFiles():
                inputsandbox += super(DiracBase,self).master_prepare(sjc)
            return inputsandbox
        return []


    def _setup_subjob_dataset(self, dataset):
        return None
    
    def _setup_bulk_subjobs(self, dirac_ids, dirac_script):
        f = open(dirac_script,'r')
        parametric_datasets = get_parametric_datasets(f.read().split('\n'))
        f.close()
        if len(parametric_datasets) != len(dirac_ids):
            raise BackendError('Dirac','Missmatch between number of datasets defines in dirac API script and those returned by DIRAC')
            
        from Ganga.GPIDev.Lib.Job.Job import Job
        master_job=self.getJobObject()
        for i in range(len(dirac_ids)):
            j=Job()
            j.copyFrom(master_job)
            j.splitter = None
            j.backend.id = dirac_ids[i]
            j.id = i
            j.inputdata = self._setup_subjob_dataset(parametric_datasets[i])
            j.status = 'submitted'
            j.time.timenow('submitted')
            master_job.subjobs.append(j)
        master_job._commit()
        return True

    def _common_submit(self, dirac_script, server):
        '''Submit the job via the Dirac server.'''
        self.id = None
        self.actualCE = None
        self.status = None
        dirac_cmd = """execfile(\'%s\')""" % dirac_script
        result = server.execute(dirac_cmd)
        ## Could use the below code instead to submit on a thread
        ## If submitting many then user may terminate ganga before
        ## all jobs submitted
#        def submit_checker(result, job, script):
#            err_msg = 'Error submitting job to Dirac: %s' % str(result)
#            if not result_ok(result) or not result.has_key('Value'):
#                logger.error(err_msg)
#                raise BackendError('Dirac',err_msg)
#            
#            idlist = result['Value']
#            if type(idlist) is list:
#                return job._setup_bulk_subjobs(idlist, script)
#            job.id = idlist
#        server.execute_nonblocking(dirac_cmd, callback_func=submit_checker, args=(self, dirac_script))
#        return True

        err_msg = 'Error submitting job to Dirac: %s' % str(result)
        if not result_ok(result) or not result.has_key('Value'):
            logger.error(err_msg)
            raise BackendError('Dirac',err_msg)
        
        idlist = result['Value']
        if type(idlist) is list:
            return self._setup_bulk_subjobs(idlist, dirac_script)
        
        self.id = idlist
        return type(self.id) == int
   

    def _addition_sandbox_content(self, subjobconfig):
        '''any additional files that should be sent to dirac'''
        return []
        
    def submit(self, subjobconfig, master_input_sandbox):
        """Submit a DIRAC job"""
        j = self.getJobObject()

        sboxname = j.createPackedInputSandbox(subjobconfig.getSandboxFiles())
        
        input_sandbox   = master_input_sandbox[:]
        input_sandbox  += sboxname
        #why send this?
        #input_sandbox  += [dirac_script_filename]

        
        input_sandbox  += self._addition_sandbox_content(subjobconfig)
        
        dirac_script = subjobconfig.getExeString().replace('##INPUT_SANDBOX##',str(input_sandbox))

        dirac_script_filename = os.path.join(j.getInputWorkspace().getPath(),'dirac-script.py')
        f=open(dirac_script_filename,'w')
        f.write(dirac_script)
        f.close()
        return self._common_submit(dirac_script_filename, dirac_ganga_server)
 
    def master_auto_resubmit(self,rjobs):
        '''Duplicate of the IBackend.master_resubmit but hooked into auto resubmission
        such that the monitoring server is used rather than the user server'''
        from Ganga.Core import IncompleteJobSubmissionError, GangaException
        from Ganga.Utility.logging import log_user_exception
        incomplete = 0
        def handleError(x):
            if incomplete:
                raise x
            else:
                return 0            
        try:
            for sj in rjobs:
                fqid = sj.getFQID('.')
                logger.info("resubmitting job %s to %s backend",fqid,sj.backend._name)
                try:
                    b = sj.backend
                    sj.updateStatus('submitting')
                    result = b._resubmit(dirac_monitoring_server)
                    if result:
                        sj.updateStatus('submitted')
                        #sj._commit() # PENDING: TEMPORARY DISABLED
                        incomplete = 1
                    else:
                        return handleError(IncompleteJobSubmissionError(fqid,'resubmission failed'))
                except Exception,x:
                    log_user_exception(logger,debug=isinstance(x,GangaException))
                    return handleError(IncompleteJobSubmissionError(fqid,str(x)))
        finally:
            master = self.getJobObject().master
            if master:
                master.updateMasterJobStatus()
        return 1

    def resubmit(self):
        """Resubmit a DIRAC job"""
        return self._resubmit(dirac_ganga_server)

    def _resubmit(self, server):
        """Resubmit a DIRAC job"""
        j=self.getJobObject()
        parametric = False
        script_path = os.path.join(j.getInputWorkspace().getPath(),
                                       'dirac-script.py')
        ## Check old script
        if j.master is None and not os.path.exists(script_path):
             raise BackendError('Dirac','No "dirac-script.py" found in j.inputdir')
        if j.master is not None and not os.path.exists(script_path):
             script_path = os.path.join(j.master.getInputWorkspace().getPath(),
                                        'dirac-script.py')
             if not os.path.exists(script_path):
                  raise BackendError('Dirac','No "dirac-script.py" found in j.inputdir or j.master.inputdir')
             parametric = True

        ## Read old script
        f=open(script_path,'r')
        script = f.read()
        f.close()

        ## Create new script - ##note instead of using get_parametric_dataset could just use j.inputdata.
        if parametric is True:
            parametric_datasets = get_parametric_datasets(script.split('\n'))
            if len(parametric_datasets) != len(j.master.subjobs):
                raise BackendError('Dirac','number of parametric datasets defined in API script doesn\'t match number of master.subjobs')
            if set(parametric_datasets[j.id]).symmetric_difference(set([f.name for f in j.inputdata.files])):
                raise BackendError('Dirac','Mismatch between dirac-script and job attributes.')
            script = script.replace('.setParametricInputData(%s)' % str(parametric_datasets),
                                    '.setInputData(%s)' % str(parametric_datasets[j.id]))
            script = script.replace('%n',str(j.id)) #name

        start_user_settings = '# <-- user settings\n'
        new_script = script[:script.find(start_user_settings) + len(start_user_settings)]

        job_ident = get_job_ident(script.split('\n'))
        for key, value in self.settings.iteritems():
             if type(value)is type(''):
                  new_script += '%s.set%s("%s")\n' % (job_ident, key, value)
             else:
                  new_script += '%s.set%s(%s)\n' % (job_ident, key, str(value))
        new_script += script[script.find('# user settings -->'):]
             

        ## Save new script
        new_script_filename = os.path.join(j.getInputWorkspace().getPath(),
                              'dirac-script.py')
        f = open(new_script_filename, 'w')
        f.write(new_script)
        f.close()
        
        return self._common_submit(new_script_filename, server)

    def reset(self, doSubjobs =False):
        """Resets the state of a job back to 'submitted' so that the
        monitoring will run on it again."""        
        j = self.getJobObject()

        disallowed = ['submitting','killed']
        if j.status in disallowed:
            logger.warning("Can not reset a job in status '%s'." % j.status)
        else:
            j.getOutputWorkspace().remove(preserve_top=True)
            j.updateStatus('submitted')
            if j.subjobs and not doSubjobs:
                logger.info('This job has subjobs, if you would like the backends '\
                            'of all the subjobs that are in status=\'completing\' or '\
                            'status=\'failed\' also reset then recall reset with the '\
                            'arg \'True\' i.e. job(3).backend.reset(True)')
            elif j.subjobs and doSubjobs:
                logger.info('resetting the backends of \'completing\' and \'failed\' subjobs.')
                for sj in j.subjobs:
                    if sj.status == 'completing' or sj.status == 'failed': sj.backend.reset()
            if j.master: j.master.updateMasterJobStatus()
    
    def kill(self):
        """ Kill a Dirac jobs"""         
        if not self.id: return None
        dirac_cmd = 'kill(%d)' % self.id
        result = dirac_ganga_server.execute(dirac_cmd)
        if not result_ok(result):
            raise BackendError('Dirac','Could not kill job: %s' % str(result))
        return result['OK']

    def peek(self,filename=None,command=None):
        """Peek at the output of a job (Note: filename/command are ignored)."""
        dirac_cmd = 'peek(%d)' % self.id
        result = dirac_ganga_server.execute(dirac_cmd)
        if result_ok(result): print result['Value']
        else: logger.error("No peeking available for Dirac job '%i'.", self.id)

    def getOutputSandbox(self,dir=None):
        j = self.getJobObject()
        if dir is None: dir = j.getOutputWorkspace().getPath()
        dirac_cmd = "getOutputSandbox(%d,'%s')" \
                    % (self.id,dir)
        result = dirac_ganga_server.execute(dirac_cmd)
        if not result_ok(result):
            msg = 'Problem retrieving output: %s' % str(result)
            logger.warning(msg)
            return False

        return True

    def removeOutputData(self):
        """
        Remove all the LFNs associated with this job.
        """
        ## Note when the API can accept a list for removeFile I will change this.
        import tempfile
        j = self.getJobObject()
        lfns = DiracBase.getOutputDataLFNs(self)
        lfn_file_name=''
        with tempfile.NamedTemporaryFile(delete=False) as lfn_file:
            lfn_file_name = lfn_file.name
            for lfn in lfns:
                lfn_file.write(lfn+'\n')
        dirac_ganga_server.execute('dirac-dms-remove-lfn %s' % lfn_file_name, shell=True)
        os.remove(lfn_file_name)


    def getOutputData(self,dir=None,names=None, force=False):
        """Retrieve data stored on SE to dir (default=job output workspace).
        If names=None, then all outputdata is downloaded otherwise names should
        be a list of files to download. If force=True then data will be redownloaded
        even if the file already exists.

        Note that if called on a master job then all subjobs outputwill be downloaded.
        If dir is None then the subjobs output goes into their individual
        outputworkspaces as expected. If however one specifies a dir then this is
        treated as a top dir and a subdir for each job will be created below it. This
        will avoid overwriting files with the same name from each subjob.
        """
        from GangaDirac.Lib.Files.DiracFile import DiracFile
        j = self.getJobObject()
        if dir is not None and not os.path.exists(dir) :
            raise GangaException("Designated outupt path '%s' must exist" % dir)

        def diracfile_getter(diracfiles):
            for df in diracfiles:
                if df.subfiles:
                    for sf in df.subfiles:
                        if sf.lfn!='' and (names is None or sf.namePattern in names):
                            yield sf
                else:
                    if df.lfn!='' and (names is None or df.namePattern in names):
                        yield df

        suceeded=[]
        if j.subjobs:
            for sj in j.subjobs:
                for df in diracfile_getter([f for f in sj.outputfiles if isinstance(f, DiracFile)] +
                                           [f for f in sj.non_copyable_outputfiles if isinstance(f, DiracFile)]):
                    output_dir = sj.getOutputWorkspace().getPath()
                    if dir is not None:
                        output_dir = os.path.join(dir, sj.fqid)
                        os.mkdir(output_dir)
                    df.localDir = output_dir
                    if os.path.exists(os.path.join(output_dir,os.path.basename(df.lfn))) and not force:
                        continue
                    try: 
                        df.get()
                        suceeded.append(df.lfn)
                    except GangaException, e: # should really make the get method throw if doesn't suceed. todo
                        logger.warning(e)
        else:
            for df in diracfile_getter([f for f in j.outputfiles if isinstance(f, DiracFile)] +
                                       [f for f in j.non_copyable_outputfiles if isinstance(f, DiracFile)]):
                df.localDir = j.getOutputWorkspace().getPath()
                if dir is not None:
                    df.localDir = dir
                if os.path.exists(os.path.join(df.localDir, os.path.basename(df.lfn))) and not force:
                    continue
                try:
                    df.get()
                    suceeded.append(df.lfn)
                except GangaException, e:
                    logger.warning(e)

        return suceeded
            
    def getOutputDataLFNs(self):
        """Retrieve the list of LFNs assigned to outputdata"""   
        from GangaDirac.Lib.Files.DiracFile import DiracFile
        j = self.getJobObject()
        lfns=[]
        
        def job_lfn_getter(job):
            def lfn_getter(diracfile):
                if diracfile.lfn != "":
                    lfns.append(diracfile.lfn)
                lfns.extend((f.lfn for f in diracfile.subfiles if f.lfn != "")) 
            map(lfn_getter, (f for f in job.outputfiles              if isinstance(f, DiracFile)))
            map(lfn_getter, (f for f in job.non_copyable_outputfiles if isinstance(f, DiracFile)))
        
        if j.subjobs:
            map(job_lfn_getter, j.subjobs)
        else:
            job_lfn_getter(j)
        return lfns
        
    def debug(self):
        '''Obtains some (possibly) useful DIRAC debug info. '''
        # check services
        cmd = 'getServicePorts()'
        result = dirac_ganga_server.execute(cmd)
        if not result_ok(result):
            logger.warning('Could not obtain services: %s' % str(result))
            return
        services = result.get('Value',{})
        for category in services:
            system,service = category.split('/')
            cmd = "ping('%s','%s')" % (system,service)
            result = dirac_ganga_server.execute(cmd)
            msg = 'OK.'
            if not result_ok(result): msg = '%s' % result['Message']
            print '%s: %s' %  (category,msg)
        # get pilot info for this job
        if type(self.id) != int: return
        j = self.getJobObject()
        cwd = os.getcwd()
        debug_dir = j.getDebugWorkspace().getPath()
        cmd = "getJobPilotOutput(%d,'%s')" % \
              (self.id, debug_dir)
        result = dirac_ganga_server.execute(cmd)
        #print 'result =', result
        if result_ok(result):
            print 'Pilot Info: %s/pilot_%d/std.out.'%(debug_dir,self.id)
        else:
            print result.get('Message','')

    def _getStateTime(job, status):
        """Returns the timestamps for 'running' or 'completed' by extracting
        their equivalent timestamps from the loggingInfo."""
        ## Now private to stop server cross-talk from user thread. Since updateStatus calles
        ## this method whether called itself by the user thread or monitoring thread.
        ## Now don't use hook but define our own private version
        ## used in monitoring loop... messy but works.
        if job.status != status:
            b_list = ['running', 'completing', 'completed', 'failed']
            backend_final = ['failed', 'completed']
            #backend stamps
            if not job.subjobs and status in b_list: 
                for childstatus in b_list:
                    if job.backend.id:
                        logger.debug("Accessing getStateTime() in diracAPI")
                        dirac_cmd = "getStateTime(%d,\'%s\')" % (job.backend.id, childstatus)
                        be_statetime = dirac_monitoring_server.execute(dirac_cmd)
                        if childstatus in backend_final:
                            job.time.timestamps["backend_final"] = be_statetime 
                            logger.debug("Wrote 'backend_final' to timestamps.")
                        else:
                            job.time.timestamps["backend_"+childstatus] = be_statetime 
                            logger.debug("Wrote 'backend_%s' to timestamps.", childstatus)
                    if childstatus==status: break
            logger.debug("_getStateTime(job with id: %d, '%s') called.", job.id, job.status)
        else:
            logger.debug("Status changed from '%s' to '%s'. No new timestamp was written", job.status, status)
    _getStateTime = staticmethod(_getStateTime)

    def timedetails(self):
        """Prints contents of the loggingInfo from the Dirac API."""
        if not self.id: return None
        logger.debug("Accessing timedetails() in diracAPI")
        dirac_cmd = 'timedetails(%d)' % self.id
        return dirac_ganga_server.execute(dirac_cmd)
    
    def updateMonitoringInformation(jobs):
        """Check the status of jobs and retrieve output sandboxes"""
        ## Only those jobs in 'submitted','running' are passed in here for checking
        from Ganga.Core import monitoring_component
        ganga_job_status = [ j.status for j in jobs ]
      #  dirac_job_ids = [j.backend.id for j in jobs ]
##         for j in jobs: dirac_job_ids.append(j.backend.id)
        if not dirac_monitoring_server.proxyValid():
            if DiracBase.dirac_monitoring_is_active:
                logger.warning('DIRAC monitoring inactive (no valid proxy '\
                               'found).')
                DiracBase.dirac_monitoring_is_active = False
            return
        else:
            DiracBase.dirac_monitoring_is_active = True

        # now that can submit in non_blocking mode, can see jobs in submitting
        # that have yet to be assigned an id so ignore them 
        cmd = 'status(%s)' % str([j.backend.id for j in jobs if j.backend.id is not None])
        result = dirac_monitoring_server.execute(cmd)
        if type(result) != type([]):
            logger.warning('DIRAC monitoring failed: %s' % str(result))
            return
                
        #thread_handled_states = {'completed':'completing', 'failed':'failed'}
        #thread_code           = {'completing':__completed_finalise, 'failed':__failed_finalise}
        for job, state, old_state in zip(jobs, result, ganga_job_status):
            if monitoring_component:
                if monitoring_component.should_stop(): break

            job.backend.statusInfo = state[0]
            job.backend.status     = state[1]
            job.backend.actualCE   = state[2]
            
            ## Is this really catching a real problem?
            if job.status != old_state:
                logger.warning('User changed Ganga job status from %s -> %s' % (str(old_state),j.status))
                continue
            ####################
            updated_status = state[3]
            
            if updated_status == job.status: continue
 
            if updated_status == 'failed':
                DiracBase._getStateTime(job,'failed')
                job.updateStatus('failed')
                if getConfig('DIRAC')['failed_sandbox_download']:
                    dirac_monitoring_server.execute_nonblocking("getOutputSandbox(%d,'%s')" % (job.backend.id, job.getOutputWorkspace().getPath()),
                                                                priority=7)
            elif updated_status == 'completed':
                def job_finalisation(result, job):
                    cmd = 'normCPUTime(%d)' % job.backend.id
                    job.backend.normCPUTime = dirac_monitoring_server.execute(cmd)
                    file_info_dict = dirac_monitoring_server.execute('getOutputDataInfo(%d)'% job.backend.id)

                    wildcards = [f.namePattern for f in job.outputfiles if regex.search(f.namePattern) is not None]
                    
                    if not os.path.exists(os.path.join(job.getOutputWorkspace().getPath(), getConfig('Output')['PostProcessLocationsFileName'])):##REMOVE this check before commiting
                        with open(os.path.join(job.getOutputWorkspace().getPath(), getConfig('Output')['PostProcessLocationsFileName']),'wb') as postprocesslocationsfile:
                            for file_name, info in file_info_dict.iteritems():
                                valid_wildcards = ['']
                                if (wc for wc in wildcards if fnmatch.fnmatch(file_name, wc)):
                                    valid_wildcards = [wc for wc in wildcards if fnmatch.fnmatch(file_name, wc)]
                            
                                for wc in valid_wildcards:
                                    postprocesslocationsfile.write('DiracFile:::%s&&%s->%s:::%s:::%s\n'% (wc,
                                                                                                          file_name,
                                                                                                          info.get('LFN','Error Getting LFN!'),
                                                                                                          str(info.get('LOCATIONS',['NotAvailable'])),
                                                                                                          info.get('GUID','NotAvailable')
                                                                                                          ))
                    
                    if not result_ok(result):
                        logger.warning('Problem retrieving outputsandbox: %s' % str(result))
                        DiracBase._getStateTime(job,'failed')
                        job.updateStatus('failed')
                        return
                        ## This should no-longer be the case but keep it around so know the command if ppl need it
#                        if job.outputdata:
#                            r = DiracBase.dirac_monitoring_server.execute('print getOutputDataLFNs(%d)' % job.backend.id,
#                                                                          priority=5)
                    DiracBase._getStateTime(job,'completed')
                    job.updateStatus('completed')
                    if job.master: job.master.updateMasterJobStatus()
                DiracBase._getStateTime(job,'completing')
                job.updateStatus('completing')
                dirac_monitoring_server.execute_nonblocking("getOutputSandbox(%d,'%s')" % (job.backend.id, job.getOutputWorkspace().getPath()),
                                                            priority=5,
                                                            callback_func=job_finalisation,
                                                            args=(job,))
            else:
                #updated_status = thread_handled_states[updated_status]
                DiracBase._getStateTime(job,updated_status)
                job.updateStatus(updated_status)
                  
    updateMonitoringInformation = staticmethod(updateMonitoringInformation)

    def execAPI(cmd,timeout=getConfig('DIRAC')['Timeout']):
        """Executes DIRAC API commands.  If variable 'result' is set, then
        it is returned by this method. """
        return dirac_ganga_server.execute(cmd, timeout)

    execAPI = staticmethod(execAPI)

#    def execAPI_async(cmd,timeout=getConfig('DIRAC')['Timeout']):
#        """Executes DIRAC API commands.  If variable 'result' is set, then
#        it is returned by this method. """
#        return dirac_ganga_server.execute_nonblocking(cmd, timeout, priority=4)

#    execAPI_async = staticmethod(execAPI_async)

#    def getQueues():
#        output=''
#        output+= '{0:^55} | {1:^50}\n'.format('Ganga user threads:','Ganga monitoring threads:')
#        output+= '{0:^55} | {1:^50}\n'.format('------------------', '------------------------')
#        output+= '{0:<10} {1:<33} {2:<10} | {0:<10} {1:<33} {2:<10}\n'.format('Name', 'Command', 'Timeout')
#        output+= '{0:<10} {1:<33} {2:<10} | {0:<10} {1:<33} {2:<10}\n'.format('----', '-------', '-------')
#        for u, m in zip( dirac_ganga_server.worker_status(),
#                         dirac_monitoring_server.worker_status() ):
#            # name has extra spaces as colour characters are invisible but still count
#            name_user    = getColour('fg.red') + u[0] + getColour('fg.normal')
#            name_monitor = getColour('fg.red') + m[0] + getColour('fg.normal')
#            if u[1] == 'idle':
#                name_user = name_user.replace(getColour('fg.red'), getColour('fg.green'))
#            if m[1] == 'idle':
#                name_monitor = name_monitor.replace(getColour('fg.red'), getColour('fg.green'))
#            output+= '{0:<21} {1:<33} {2:<10} | {3:<21} {4:<33} {5:<10}\n'.format(name_user, u[1][:30], u[2], name_monitor, m[1][:30], m[2])
#
#        output+= '\n'
#        output+= "Ganga user queue:\n"
#        output+= "----------------\n"
#        output+= str([i.command_input.command for i in dirac_ganga_server.get_queue()])
#        
#        output+= '\n'
#        output+= "Ganga monitoring queue:\n"
#        output+= "----------------------\n"
#        output+= str([i.command_input.command for i in dirac_monitoring_server.get_queue()])
#        return output

#    getQueues = staticmethod(getQueues)
#\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\/\#

