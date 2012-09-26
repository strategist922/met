import copy
import logging
import math
import Stats
import Actuator
import decisionmaker_config

class DecisionMaker(object):



    def __init__(self):
        self._machtoadd = 1
        self._reconfigure = True
        self._stats = Stats.Stats()
        self._actuator = Actuator.Actuator(self._stats)
        #current state of the system - initially is empty
        self._machine_type = {}
        self._current_config = {}
        #DECISION MAKER PARAMETERS
        self._CPU_IDLE_MIN = decisionmaker_config.cpu_idle_min
        self._IO_WAIT_MAX = decisionmaker_config.io_wait_max
        self._CRITICAL_PERC = decisionmaker_config.critialStatePercentage
        self._READ_WRITE_DISTANCE_MIN = decisionmaker_config.read_write_distance_min

        logging.info('DecisionMaker started.')


    #UTIL METHODS -----------------------------------------------------------------------------------------------------

    def isRegionServerDying(self,rstats):
        res = False
        #condition that evaluates if the RegionServer is overloaded
        if (float(rstats['cpu_idle']) < self._CPU_IDLE_MIN or float(rstats['cpu_wio']) > self._IO_WAIT_MAX):
            logging.info('cpu_idle:',rstats['cpu_idle']," cpu_wio:",rstats['cpu_wio'])
            res = True
        return res

    def tagRegion(self,rstats):
        tag = 'rw'
        scantsize = float(rstats[2])
        reads = float(rstats[0])
        writes = float(rstats[1])
        totalreqs = reads + writes

        if reads == 0.0:
            reads = scantsize

        if reads == 0.0:
            scanratio = 0.0
        else:
            scanratio = scantsize /reads

        if scanratio >= 3.0:
            tag = 's'
        else:
            if totalreqs!=0:
                percReads = reads / totalreqs
                percWrites = writes / totalreqs
                if math.fabs(percReads-percWrites) > self._READ_WRITE_DISTANCE_MIN:
                    if percReads > percWrites:
                        tag = 'r'
                    else:
                        tag = 'w'

        return tag,totalreqs


    def isHalf(self,v):
        v = round(v,1)
        mv = v + 0.5
        rm = mv % 1
        if rm == 0:
            return True
        else:
            return False

    #ASSIGN MACHINES TO TYPES OF HBASE NODE CONFIGURATIONS
    def tagging(self,regionStats,nregionservers):

        regionTags = {}
        tag_count = {'rw':0,'s':0,'r':0,'w':0}
        tag_order = ['rw','s','r','w']
        nregions = 0

        #tag each region according to request patterns
        for region in regionStats.keys():
            if not region.startswith('-ROOT') and not region.startswith('.META'):
                tag_,reqs = self.tagRegion(regionStats[region])
                regionTags[region] = (tag_,reqs)
                tag_count[tag_] = tag_count[tag_] + 1
                nregions = nregions + 1

        #calculate the number of machines to assign to each tag
        machines_per_tag = {}
        machines_per_tag_float = {}
        flagged = []
        res_total = 0
        for tag in tag_order:
            tmp_perc =  float(tag_count[tag]) / float(nregions)
            tempvalue = tmp_perc * nregionservers
            flagg = self.isHalf(tempvalue)
            if flagg:
                flagged.append(tag)
            machines_ = round(tempvalue)
            machines_per_tag[tag] =	machines_
            machines_per_tag_float[tag] = tempvalue
            res_total = res_total + machines_

        logging.info('Number of Regions:',nregions)
        logging.info('Machines per tag:',machines_per_tag)

        #treat the case where the round function originates errors
        serverdiff = res_total - nregionservers
        if(serverdiff>0):
            #need to remove machines
            if not flagged:
                min_perc = machines_per_tag_float['rw']
                min_tag = 0
                for tag in tag_order:
                    if machines_per_tag_float[tag]<=min_perc:
                        min_perc = machines_per_tag_float[tag]
                        min_tag = tag
                machines_per_tag[min_tag] = machines_per_tag[min_tag]-1
            else:
                for i in range(0,int(serverdiff)):
                    print flagged
                    tagtouse = flagged.pop()
                    if machines_per_tag[tagtouse]>0:
                        machines_per_tag[tagtouse] = machines_per_tag[tagtouse]-1

        elif(serverdiff<0):
            #need to add machines
            machines_per_tag['rw'] = machines_per_tag['rw'] + abs(serverdiff)

        return machines_per_tag,regionTags


    def assignpertag(self,regions, nmachines):

        assignment = {}
        if nmachines > 0:
            for i in range(0,nmachines):
                assignment[i] = {}
                assignment[i]['load'] = 0
                assignment[i]['len'] = 0

            rmax = int(math.ceil(len(regions) / nmachines))

            #REGIONS ASSIGNMENT
            tmpmachines = copy.deepcopy(assignment.keys())
            #print 'initmachines:', tmpmachines

            while (len(regions)>0):

                region,req = regions.pop()
                binmostempty = (None,None)
                for machine in tmpmachines:
                    if binmostempty[0] == None:
                        binmostempty = (machine,assignment[machine]['load'])
                    if (binmostempty[1] > assignment[machine]['load']):
                        binmostempty = (machine,assignment[machine]['load'])

                if assignment[binmostempty[0]]['len'] < rmax:
                    assignment[binmostempty[0]][region]=req
                    assignment[binmostempty[0]]['load'] = assignment[binmostempty[0]]['load'] + req
                    assignment[binmostempty[0]]['len'] = assignment[binmostempty[0]]['len'] + 1
                else:
                    tmpmachines.remove(binmostempty[0])
                    regions.append((region,req))


        return assignment, regions


    #BINPACKING PROCEDURE
    def minimizemakespan(self,tag_to_machines,region_to_tag_reqs):
        readregions = []
        writeregions = []
        scanregions = []
        rwregions = []
        for region in region_to_tag_reqs:
            rtag = region_to_tag_reqs[region][0]
            rreqs = region_to_tag_reqs[region][1]
            if rtag == 'r':
                readregions.append((region,rreqs))
            elif rtag == 'w' :
                writeregions.append((region,rreqs))
            elif rtag == 's':
                scanregions.append((region,rreqs))
            elif rtag == 'rw':
                rwregions.append((region,rreqs))

        # If number of assgined machines is 0 for a specific tag but there are regions in that tag assign them to rw
        if(tag_to_machines['r']==0.0 and len(readregions)>0):
            rwregions = rwregions+readregions
            readregions = []
        if(tag_to_machines['w']==0.0 and len(writeregions)>0):
            rwregions = rwregions+writeregions
            writeregions = []
        if(tag_to_machines['s']==0.0 and len(scanregions)>0):
            rwregions = rwregions+scanregions
            scanregions = []

        readregions = sorted(readregions,key=lambda tupl: tupl[1])
        writeregions = sorted(writeregions,key=lambda tupl: tupl[1])
        scanregions = sorted(scanregions,key=lambda tupl: tupl[1])
        rwregions = sorted(rwregions,key=lambda tupl: tupl[1])

        nread = int(tag_to_machines['r'])
        nwrite = int(tag_to_machines['w'])
        nrw = int(tag_to_machines['rw'])
        nscan = int(tag_to_machines['s'])

        readmachines,readcopy = self.assignpertag(readregions,nread)
        writemachines, writecopy = self.assignpertag(writeregions,nwrite)
        scanmachines,scancopy = self.assignpertag(scanregions,nscan)
        rwmachines, rwcopy = self.assignpertag(rwregions,nrw)

        logging.info('ASSIGNMENT:')
        logging.info('read:',readmachines)
        logging.info('write:',writemachines)
        logging.info('scan:',scanmachines)
        logging.info('rw:',rwmachines)
        logging.info('LEFTOVERS:',readcopy,writecopy,scancopy,rwcopy)

        return readmachines,writemachines,scanmachines,rwmachines


    #-----MINIMIZE MOVES------------------------------------
    def getClosest(self,regions,mtype,cur):
        res = None
        sim = 0
        for item in cur.keys():
            curtype = self._machine_type[item]
            if curtype == mtype:
                similar = 0
                for reg in cur[item].keys():
                    if reg in regions:
                        similar = similar + 1
                if similar > sim:
                    sim = similar
                    res = item
        return res


    def getPhysical(self,readmachines,writemachines,scanmachines,rwmachines):

        result = {}
        partialResult = {}
        available_machines = self._stats.getRegionServers()
        newNMachines = len(available_machines)

        creadmachines = copy.deepcopy(readmachines)
        cwritemachines = copy.deepcopy(writemachines)
        cscanmachines = copy.deepcopy(scanmachines)
        crwmachines = copy.deepcopy(rwmachines)

        cur = copy.deepcopy(self._current_config)

        if len(self._current_config)!=0:
            #NEED TO MINIMIZE MOVES
            if len(self._current_config) <= newNMachines:
                #NEW MACHINES TO ACCOMODATE
                newmachines = []
                for item in available_machines:
                    if item not in self._machine_type.keys():
                        newmachines.append(item)
                print newmachines

                for item in readmachines.keys():
                    physical = self.getClosest(readmachines[item],'r',cur)
                    if not physical is None:
                        self._machine_type[physical] = 'r'
                        result[physical] = readmachines[item]
                        del creadmachines[item]
                        del cur[physical]

                for item in writemachines.keys():
                    physical = self.getClosest(writemachines[item],'w',cur)
                    if not physical is None:
                        self._machine_type[physical] = 'w'
                        result[physical] = writemachines[item]
                        del cwritemachines[item]
                        del cur[physical]

                for item in scanmachines.keys():
                    physical = self.getClosest(scanmachines[item],'s',cur)
                    if not physical is None:
                        self._machine_type[physical] = 's'
                        result[physical] = scanmachines[item]
                        del cscanmachines[item]
                        del cur[physical]

                for item in rwmachines.keys():
                    physical = self.getClosest(rwmachines[item],'rw',cur)
                    if not physical is None:
                        self._machine_type[physical] = 'rw'
                        result[physical] = rwmachines[item]
                        del crwmachines[item]
                        del cur[physical]

                #at this point every machine was matched to a possible assignment
                #next step is to check for missing assignments and possible change of configs
                machinesleft = creadmachines.keys()+cwritemachines.keys()+cscanmachines.keys()+crwmachines.keys()+newmachines

                for item in creadmachines.keys():
                    physical = machinesleft.pop()
                    self._machine_type[physical] = 'r'
                    self._actuator.configureServer(physical,'r')
                    result[physical] = readmachines[item]

                for item in cwritemachines.keys():
                    physical = machinesleft.pop()
                    self._machine_type[physical] = 'w'
                    self._actuator.configureServer(physical,'w')
                    result[physical] = writemachines[item]

                for item in cscanmachines.keys():
                    physical = machinesleft.pop()
                    self._machine_type[physical] = 's'
                    self._actuator.configureServer(physical,'s')
                    result[physical] = scanmachines[item]

                for item in crwmachines.keys():
                    physical = machinesleft.pop()
                    self._machine_type[physical] = 'rw'
                    self._actuator.configureServer(physical,'rw')
                    result[physical] = rwmachines[item]

                #MOVE REGIONS INTO PLACE IF NEEDED
                self._actuator.distributeRegionsPerRS(result,self._machine_type)

            else:
                #ATTENTION: CURRENTLY NOT CONSIDERING THE CASE WHERE THERE ARE FEWER MACHINES!
                logging.info( 'Machines removed... DOING NOTHING')

        else:
            #FIRST RECONFIGURATION
            logging.info('Current state empty. First reconfig.')

            for item in readmachines.keys():
                physical = available_machines.pop()
                self._machine_type[physical] = 'r'
                self._actuator.configureServer(physical,'r',available_machines)
                result[physical] = readmachines[item]
                partialResult[physical] = readmachines[item]
                self._actuator.distributeRegionsPerRS(partialResult,self._machine_type)
                partialResult = {}

            for item in writemachines.keys():
                physical = available_machines.pop()
                self._machine_type[physical] = 'w'
                self._actuator.configureServer(physical,'w',available_machines)
                result[physical] = writemachines[item]
                partialResult[physical] = writemachines[item]
                self._actuator.distributeRegionsPerRS(partialResult,self._machine_type)
                partialResult = {}

            for item in scanmachines.keys():
                physical = available_machines.pop()
                self._machine_type[physical] = 's'
                self._actuator.configureServer(physical,'s',available_machines)
                result[physical] = scanmachines[item]
                partialResult[physical] = scanmachines[item]
                self._actuator.distributeRegionsPerRS(partialResult,self._machine_type)
                partialResult = {}

            for item in rwmachines.keys():
                physical = available_machines.pop()
                self._machine_type[physical] = 'rw'
                self._actuator.configureServer(physical,'rw',available_machines)
                result[physical] = rwmachines[item]
                partialResult[physical] = rwmachines[item]
                print 'partialResult', partialResult
                self._actuator.distributeRegionsPerRS(partialResult,self._machine_type)
                partialResult = {}


        logging.info('FINAL DISTRIBUTION:' ,result)
        self._current_config = result
        return result


    #MAIN METHOD -----------------------------------------------------------------------------------------------------


    def cycle(self,bigbang):

        regionServerList = self._stats.getRegionServers()

        actionNeeded = False
        machdying = 0
        nmach = 0

        #check if any of the regionServers is dying
        for rsKey in regionServerList:
            dying = self.isRegionServerDying(self._stats.getRegionServerStats(rsKey))
            logging.info(rsKey," is dying? ",dying)
            if dying:
                machdying = machdying + 1
                actionNeeded = True
            nmach = nmach + 1

        #CHECK IF WE NEED TO ADD/REMOVE MACHINES to address critical state
        if machdying/nmach > self._CRITICAL_PERC or bigbang:
            #cluster in bad shape - add machines
            self._reconfigure = False

        #If we need to reconfigure stuff then:
        if actionNeeded and self._reconfigure:
            nregionservers = self._stats.getNumberRegionServers()
            regionStats = self._stats.getRegionStats()
            tagged_machines,tagged_regions = self.tagging(regionStats,nregionservers)
            #going for ASSIGNMENT ALGORITHM
            readmachines,writemachines,scanmachines,rwmachines = self.minimizemakespan(tagged_machines,tagged_regions)
            #define which physical machine is going to accomodate which config (function 'f')
            self.getPhysical(readmachines,writemachines,scanmachines,rwmachines)
            self._reconfigure = False

        elif actionNeeded and not self._reconfigure:
            #CALL TIRAMOLA TO ADD OPENSTACK MACHINES
            logging.info('CALLING TIRAMOLA TO ADD MACHINES! number of machines:',self._machtoadd)
            for i in range(0,self._machtoadd):
                self._actuator.tiramolaAddMachine()
                #NEED TO REFRESH STATS
                self._stats.refreshStats(False)
                nregionservers = self._stats.getNumberRegionServers()
                regionStats = self._stats.getRegionStats()
                #GOING FOR CONFIG WITH NEW MACHINES
                tagged_machines,tagged_regions = self.tagging(regionStats,nregionservers)
                #going for ASSIGNMENT ALGORITHM
                readmachines,writemachines,scanmachines,rwmachines = self.minimizemakespan(tagged_machines,tagged_regions)
                #define which physical machine is going to accomodate which config (function 'f')
                self.getPhysical(readmachines,writemachines,scanmachines,rwmachines)

            #Update control vars
            self._machtoadd = self._machtoadd * 2
            self._reconfigure = True

        else:
            logging.info('Cluster is healthy. Nothing to do.')
            self._machtoadd = 1


